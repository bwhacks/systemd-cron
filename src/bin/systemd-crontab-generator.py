#!/usr/bin/python3
import sys
import os
import pwd
import re
import string
from functools import reduce
import hashlib
import errno

envvar_re = re.compile(r'^([A-Za-z_0-9]+)\s*=\s*(.*)$')

MINUTES_SET = list(range(0, 60))
HOURS_SET = list(range(0, 24))
DAYS_SET = list(range(1, 32))
DOWS_SET = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
MONTHS_SET = list(range(1, 13))
TIME_UNITS_SET = ['daily', 'weekly', 'monthly', 'quarterly', 'semi-annually', 'yearly']

KSH_SHELLS = ['/bin/sh', '/bin/dash', '/bin/ksh', '/bin/bash', '/usr/bin/zsh']
REBOOT_FILE = '/run/crond.reboot'
RANDOMIZED_DELAY = @randomized_delay@
RUN_PARTS_FLAG = '/run/systemd/use_run_parts'
USE_LOGLEVELMAX = '@use_loglevelmax@'

SELF = os.path.basename(sys.argv[0])

# this is dumb, but gets the job done
PART2TIMER = {
    'apt-compat': 'apt-daily',
    'dpkg': 'dpkg-db-backup',
    'plocate': 'plocate-updatedb',
}


for pgm in ('/usr/sbin/sendmail', '/usr/lib/sendmail'):
    if os.path.exists(pgm):
        HAS_SENDMAIL = True
        break
else:
    HAS_SENDMAIL = False

class Persistent(object):
    yes, no, auto = range(3)

    @classmethod
    def parse(cls, value):
        value = value.strip().lower()
        if value in ['yes', 'true', '1']:
            return cls.yes
        elif value in ['auto', '']:
            return cls.auto
        else:
            return cls.no


def files(dirname):
    try:
        return list(filter(os.path.isfile, [os.path.join(dirname, f) for f in os.listdir(dirname)]))
    except OSError:
        return []

def expand_home_path(path, user):
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        return path

    parts = path.split(':')
    for i, part in enumerate(parts):
        if part.startswith('~/'):
            parts[i] = home + part[1:]
    return ':'.join(parts)

def environment_string(env):
    line = []
    for k, v in env.items():
        if ' ' in v:
            line.append('"%s=%s"' % (k, v))
        else:
            line.append('%s=%s' % (k, v))
    return ' '.join(line)

def parse_crontab(filename, withuser=True, monotonic=False):
    basename = os.path.basename(filename)
    environment = { }
    random_delay = 1
    start_hours_range = 0
    boot_delay = 0
    persistent = Persistent.yes if monotonic else Persistent.auto
    batch = False
    run_parts = @use_runparts@
    with open(filename, 'r', encoding='utf8') as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            envvar = envvar_re.match(line)
            if envvar:
                value = envvar.group(2)
                value = value.strip("'").strip('"')
                if envvar.group(1) == 'RANDOM_DELAY':
                     try:
                         random_delay = int(value)
                     except ValueError:
                         log(4, 'invalid RANDOM_DELAY in %s: %s' % (filename, line))
                elif envvar.group(1) == 'START_HOURS_RANGE':
                     try:
                         start_hours_range = int(value.split('-')[0])
                     except ValueError:
                         log(4, 'invalid START_HOURS_RANGE in %s: %s' % (filename, line))
                elif envvar.group(1) == 'DELAY':
                     try:
                         boot_delay = int(value)
                     except ValueError:
                         log(4, 'invalid DELAY in %s: %s' % (filename, line))
                elif envvar.group(1) == 'PERSISTENT':
                     persistent = Persistent.parse(value)
                elif not withuser and envvar.group(1) == 'PATH':
                     environment['PATH'] = expand_home_path(value, basename)
                elif envvar.group(1) == 'BATCH':
                     batch = (value.strip().lower() in ['yes','true','1'])
                elif envvar.group(1) == 'RUN_PARTS':
                     run_parts = (value.strip().lower() in ['yes','true','1'])
                elif envvar.group(1) == 'MAILTO':
                     environment[envvar.group(1)] = value
                     if value and not HAS_SENDMAIL:
                         log(4, 'a MTA is not installed, but MAILTO is set in %s' % filename)
                else:
                     environment[envvar.group(1)] = value
                continue

            parts = line.split()
            line = ' '.join(parts)

            if monotonic:
                if len(parts) < 4:
                    yield { 'l': line }
                    continue

                period, delay, jobid = parts[0:3]
                command = ' '.join(parts[3:])
                period = {
                        '1': 'daily',
                        '7': 'weekly',
                        '30': 'monthly',
                        '31': 'monthly',
                        '@biannually': 'semi-annually',
                        '@bi-annually': 'semi-annually',
                        '@semiannually': 'semi-annually',
                        '@anually': 'yearly',
                        '@annually': 'yearly',
                        }.get(period, None) or period.lstrip('@')
                try:
                    boot_delay = int(delay)
                except ValueError:
                    log(4, 'invalid DELAY in %s: %s' % (filename, line))
                    boot_delay = 0
                if boot_delay < 0: boot_delay = 0

                valid_chars = "-_%s%s" % (string.ascii_letters, string.digits)
                jobid = ''.join(c for c in jobid if c in valid_chars)

                yield {
                        'e': environment_string(environment),
                        's': environment.get('SHELL','/bin/sh'),
                        'a': random_delay,
                        'l': line,
                        'f': filename,
                        'p': period.lower(),
                        'b': boot_delay,
                        'h': start_hours_range,
                        'P': False if persistent == Persistent.no else True,
                        'j': jobid,
                        'u': 'root',
                        'c': command,
                        'Z': batch,
                        }

            else:
                if line.startswith('@'):
                    if len(parts) < 2 + int(withuser):
                        yield { 'l': line }
                        continue

                    period = parts[0]
                    period = {
                            '@biannually': 'semi-annually',
                            '@bi-annually': 'semi-annually',
                            '@semiannually': 'semi-annually',
                            '@anually': 'yearly',
                            '@annually': 'yearly',
                            }.get(period, None) or period.lstrip('@')

                    user, command = (parts[1], ' '.join(parts[2:])) if withuser else (basename, ' '.join(parts[1:]))

                    yield {
                            'e': environment_string(environment),
                            's': environment.get('SHELL','/bin/sh'),
                            'a': random_delay,
                            'l': line,
                            'f': filename,
                            'p': period.lower(),
                            'b': boot_delay,
                            'h': start_hours_range,
                            'P': False if persistent == Persistent.no else True,
                            'j': basename,
                            'u': user,
                            'c': command,
                            'Z': batch,
                            'J': run_parts,
                            }
                else:
                    if len(parts) < 6 + int(withuser):
                        yield { 'l': line }
                        continue

                    minutes, hours, days = parts[0:3]
                    months, dows = parts[3:5]
                    user, command = (parts[5], ' '.join(parts[6:])) if withuser else (basename, ' '.join(parts[5:]))

                    yield {
                            'e': environment_string(environment),
                            's': environment.get('SHELL','/bin/sh'),
                            'a': random_delay,
                            'l': line,
                            'f': filename,
                            'b': boot_delay,
                            'm': parse_time_unit(filename, line, minutes, MINUTES_SET),
                            'h': parse_time_unit(filename, line, hours, HOURS_SET),
                            'd': parse_time_unit(filename, line, days, DAYS_SET),
                            'w': parse_time_unit(filename, line, dows, DOWS_SET, dow_map),
                            'M': parse_time_unit(filename, line, months, MONTHS_SET, month_map),
                            'P': True if persistent == Persistent.yes else False,
                            'j': basename,
                            'u': user,
                            'c': command,
                            'Z': batch,
                            'J': run_parts,
                            }

def parse_time_unit(filename, line, value, values, mapping=int):
    if value == '*':
        return ['*']
    try:
        base = min(values)
        # day of weeks
        if isinstance(base, str):
            base = 0
        result = sorted(reduce(lambda a, i: a.union(set(i)), list(map(values.__getitem__,
        list(map(parse_period(mapping, base), value.split(','))))), set()))
    except ValueError:
        result = []
    if not len(result):
        log(3, 'garbled time in %s [%s]: %s' % (filename, line, value))
    return result

def month_map(month):
    try:
        return int(month)
    except ValueError:
        return ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'nov', 'dec'].index(month.lower()[0:3]) + 1

def dow_map(dow):
    try:
        return ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'].index(dow[0:3].lower())
    except ValueError:
        return int(dow) % 7

def parse_period(mapping=int, base=0):
    def parser(value):
        try:
            range, step = value.split('/')
        except ValueError:
            range = value
            step = 1

        if range == '*':
            return slice(None, None, int(step))

        try:
            start, end = range.split('-')
        except ValueError:
            start = end = range

        return slice(mapping(start) - 1 + int(not(bool(base))), mapping(end) + int(not(bool(base))), int(step))

    return parser

def generate_timer_unit(job, seq=None, unit_name=None):
    persistent = job['P']
    command = job['c']
    parts = command.split()
    testremoved = None
    standardoutput = None
    delay = job['b']
    daemon_reload = os.path.isfile(REBOOT_FILE)

    try:
        home = pwd.getpwnam(job['u']).pw_dir
    except KeyError:
        home = None

    # perform smart substitutions for known shells
    if 's' not in job or job['s'] in KSH_SHELLS:
        if home and command.startswith('~/'):
            command = home + command[1:]

        if (len(parts) >= 3 and
            parts[-2] == '>' and
            parts[-1] == '/dev/null'):
            command = ' '.join(parts[0:-2])
            parts = command.split()
            standardoutput='null';

        if (len(parts) >= 2 and
            parts[-1] == '>/dev/null'):
            command = ' '.join(parts[0:-1])
            parts = command.split()
            standardoutput='null';

        if (len(parts) == 6 and
            parts[0] == '[' and
            parts[1] in ['-x','-f','-e'] and
            parts[2] == parts[5] and
            parts[3] == ']' and
            parts[4] == '&&' ):
                testremoved = parts[2]
                command = ' '.join(parts[5:])
                parts = command.split()

        if (len(parts) == 5 and
            parts[0] == 'test' and
            parts[1] in ['-x','-f','-e'] and
            parts[2] == parts[4] and
            parts[3] == '&&' ):
                testremoved = parts[2]
                command = ' '.join(parts[4:])
                parts = command.split()

        if testremoved and not os.path.isfile(testremoved): return

        if (len(parts) == 6 and
            parts[0] == '[' and
            parts[1] in ['-d','-e'] and
            parts[2] == '/run/systemd/system' and
            parts[3] == ']' and
            parts[4] == '||'): return

        if (len(parts) == 5 and
            parts[0] == 'test' and
            parts[1] in ['-d','-e'] and
            parts[2] == '/run/systemd/system' and
            parts[3] == '||'): return

        # TODO: translate  'command%line1%line2%line3
        # in '/bin/echo -e line1\\nline2\\nline3 | command'
        # to be POSIX compliant

    if 'p' in job:
        hour = job['h'] if 'h' in job else 0

        if job['p'] == 'reboot':
            if daemon_reload: return
            if delay == 0: delay = 1
            schedule = None
            persistent = False
        elif job['p'] == 'minutely':
            schedule = job['p']
            persistent = False
        elif job['p'] == 'hourly' and delay == 0:
            schedule = 'hourly'
        elif job['p'] == 'hourly':
            schedule = '*-*-* *:%s:0' % delay
            delay = 0
        elif job['p'] == 'midnight' and delay == 0:
            schedule = 'daily'
        elif job['p'] == 'midnight':
            schedule = '*-*-* 0:%s:0' % delay
        elif job['p'] in TIME_UNITS_SET and hour == 0 and delay == 0:
            schedule = job['p']
        elif job['p'] == 'daily':
            schedule = '*-*-* %s:%s:0' % (hour, delay)
        elif job['p'] == 'weekly':
            schedule = 'Mon *-*-* %s:%s:0' % (hour, delay)
        elif job['p'] == 'monthly':
            schedule = '*-*-1 %s:%s:0' % (hour, delay)
        elif job['p'] == 'quarterly':
            schedule = '*-1,4,7,10-1 %s:%s:0' % (hour, delay)
        elif job['p'] == 'semi-annually':
            schedule = '*-1,7-1 %s:%s:0' % (hour, delay)
        elif job['p'] == 'yearly':
            schedule = '*-1-1 %s:%s:0' % (hour, delay)
        else:
            try:
               if int(job['p']) > 31:
                    # workaround for anacrontab
                    schedule = '*-1/%s-1 %s:%s:0' % (int(round(job['p']/30)), hour, delay)
               else:
                    schedule = '*-*-1/%s %s:%s:0' % (int(job['p']), hour, delay)
            except ValueError:
                    log(3, 'unknown schedule in %s: %s' % (job['f'], job['l']))
                    schedule = job['p']

    else:
        if job['w'] == ['*']:
            dows=''
        else:
            dows_sorted = []
            for day in DOWS_SET:
                if day in job['w']:
                    dows_sorted.append(day)
            dows = ','.join(dows_sorted) + ' '

        if 0 in job['M']: job['M'].remove(0)
        if 0 in job['d']: job['d'].remove(0)
        if not len(job['M']) or not len(job['d']) or not len(job['h']) or not len(job['m']):
            return
        schedule = '%s*-%s-%s %s:%s:00' % (dows, ','.join(map(str, job['M'])),
                ','.join(map(str, job['d'])), ','.join(map(str, job['h'])), ','.join(map(str, job['m'])))

    if not unit_name:
        if not persistent:
            unit_id = next(seq)
        else:
            unit_id = hashlib.md5()
            unit_id.update(bytes('\0'.join([schedule, command]), 'utf-8'))
            unit_id = unit_id.hexdigest()
        unit_name = "cron-%s-%s-%s" % (job['j'], job['u'], unit_id)

    if not (len(parts) == 1 and os.path.isfile(command)):
        with open('%s/%s.sh' % (TARGET_DIR, unit_name), 'w', encoding='utf8') as f:
            f.write(command)
        command=job['s'] + ' ' + TARGET_DIR + '/' + unit_name + '.sh'

    with open('%s/%s.timer' % (TARGET_DIR, unit_name), 'w' , encoding='utf8') as f:
        f.write('[Unit]\n')
        f.write('Description=[Timer] "%s"\n' % job['l'].replace('%', '%%'))
        f.write('Documentation=man:systemd-crontab-generator(8)\n')
        f.write('PartOf=cron.target\n')
        f.write('SourcePath=%s\n' % job['f'])
        if testremoved: f.write('ConditionFileIsExecutable=%s\n' % testremoved)

        f.write('\n[Timer]\n')
        f.write('Unit=%s.service\n' % unit_name)
        if schedule: f.write('OnCalendar=%s\n' % schedule)
        else:        f.write('OnBootSec=%sm\n' % delay)
        if 'a' in job and job['a'] != 1:
            if RANDOMIZED_DELAY:
                f.write('RandomizedDelaySec=%sm\n' % job['a'])
            else:
                f.write('AccuracySec=%sm\n' % job['a'])
        if @persistent@ and persistent: f.write('Persistent=true\n')

    try:
        os.symlink('%s/%s.timer' % (TARGET_DIR, unit_name), '%s/%s.timer' % (TIMERS_DIR, unit_name))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    with open('%s/%s.service' % (TARGET_DIR, unit_name), 'w', encoding='utf8') as f:
        f.write('[Unit]\n')
        f.write('Description=[Cron] "%s"\n' % job['l'].replace('%', '%%'))
        f.write('Documentation=man:systemd-crontab-generator(8)\n')
        f.write('SourcePath=%s\n' % job['f'])
        if re.search('MAILTO=(\s+|$)', job['e']):
            pass # mails explicitely disabled
        elif not HAS_SENDMAIL:
            pass # mails automaticaly disabled
        else:
            f.write('OnFailure=cron-failure@%i.service\n')
        if job['u'] != 'root' or job['f'] == '@statedir@/root':
            f.write('Requires=systemd-user-sessions.service\n')
            if home:
                f.write('RequiresMountsFor=%s\n' % home)

        f.write('\n[Service]\n')
        f.write('Type=oneshot\n')
        f.write('IgnoreSIGPIPE=false\n')
        f.write('KillMode=process\n')
        if USE_LOGLEVELMAX != 'no':
            f.write('LogLevelMax=%s\n' % USE_LOGLEVELMAX)
        if schedule and delay:
             f.write('ExecStartPre=-@libdir@/@package@/boot_delay %s\n' % delay)
        f.write('ExecStart=%s\n' % command)
        if job['e']:
             f.write('Environment=%s\n' % job['e'])
        if job['u'] != 'root':
             f.write('User=%s\n' % job['u'])
        if standardoutput:
             f.write('StandardOutput=%s\n' % standardoutput)
        if 'Z' in job and job['Z']:
             f.write('CPUSchedulingPolicy=idle\n')
             f.write('IOSchedulingClass=idle\n')

    return '%s.timer' % unit_name

def log(level, message):
    if len(sys.argv) == 4:
        with open('/dev/kmsg', 'w', encoding='utf8') as kmsg:
            kmsg.write('<%s>%s[%s]: %s\n' % (level, SELF, os.getpid(), message))
    else:
        sys.stderr.write('%s: %s\n' % (SELF, message))

seqs = {}
def count():
    n = 0
    while True:
        yield n
        n += 1

def main():
    try:
        os.makedirs(TIMERS_DIR)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    run_parts = @use_runparts@
    if os.path.isfile('/etc/crontab'):
        for job in parse_crontab('/etc/crontab', withuser=True):
            if 'J' in job:
                 run_parts = job['J']
            if 'c' not in job:
                 log(3, 'truncated line in /etc/crontab: %s' % job['l'])
                 continue
            if '/etc/cron.hourly'  in job['c']: continue
            if '/etc/cron.daily'   in job['c']: continue
            if '/etc/cron.weekly'  in job['c']: continue
            if '/etc/cron.monthly' in job['c']: continue
            generate_timer_unit(job, seq=seqs.setdefault(job['j']+job['u'], count()))

    CRONTAB_FILES = files('/etc/cron.d')
    for filename in CRONTAB_FILES:
        basename = os.path.basename(filename)
        masked = False
        for unit_file in ('@unitdir@/%s.timer' % basename,
                          '/etc/systemd/system/%s.timer' % basename,
                          '/run/systemd/system/%s.timer' % basename):
            if os.path.exists(unit_file):
                masked = True
                if os.path.realpath(unit_file) == '/dev/null':
                    log(5, 'ignoring %s because it is masked' % filename)
                else:
                    log(5, 'ignoring %s because native timer is present' % filename)
                break
        if masked:
            continue
        if basename.startswith('.'):
            continue
        if '.dpkg-' in basename:
            log(5, 'ignoring %s' % filename)
            continue
        if '~' in basename:
            log(5, 'ignoring %s' % filename)
            continue
        for job in parse_crontab(filename, withuser=True):
            if 'c' not in job:
                log(3, 'truncated line in %s: %s' % (filename, job['l']))
                continue
            generate_timer_unit(job, seq=seqs.setdefault(job['j']+job['u'], count()))

    if run_parts:
        open(RUN_PARTS_FLAG, 'a').close()
    else:
        if os.path.exists(RUN_PARTS_FLAG):
            os.unlink(RUN_PARTS_FLAG)
        # https://github.com/systemd-cron/systemd-cron/issues/47
        job_template = dict()
        job_template['P'] = @persistent@
        job_template['u'] = 'root'
        job_template['e'] = ''
        i = 0
        for period in ['hourly', 'daily', 'weekly', 'monthly', 'yearly']:
            i = i + 1
            job_template['b'] = i * 5
            directory = '/etc/cron.' + period
            if not os.path.isdir(directory):
                continue
            CRONTAB_FILES = files('/etc/cron.' + period)
            for filename in CRONTAB_FILES:
                job_template['p'] = period
                basename = os.path.basename(filename)
                basename_distro = PART2TIMER.get(basename, basename)
                if (os.path.exists('/lib/systemd/system/%s.timer' % basename)
                 or os.path.exists('/lib/systemd/system/%s.timer' % basename_distro)
                 or os.path.exists('/etc/systemd/system/%s.timer' % basename)):
                    log(5, 'ignoring %s because native timer is present' % filename)
                    continue
                elif basename.startswith('.'):
                    continue
                elif '.dpkg-' in basename:
                    log(5, 'ignoring %s' % filename)
                    continue
                else:
                    job = job_template
                    job['l'] = filename
                    job['f'] = filename
                    job['j'] = period + '-' + basename
                    job['c'] = filename
                    generate_timer_unit(job, unit_name='cron-' + job['j'])

    if os.path.isfile('/etc/anacrontab'):
        for job in parse_crontab('/etc/anacrontab', monotonic=True):
            if 'c' not in job:
                 log(3, 'truncated line in /etc/anacrontab: %s' % job['l'])
                 continue
            generate_timer_unit(job, seq=seqs.setdefault(job['j']+job['u'], count()))


    if os.path.isdir('@statedir@'):
        # /var is avaible
        USERCRONTAB_FILES = files('@statedir@')
        for filename in USERCRONTAB_FILES:
            basename = os.path.basename(filename)
            if '.' in basename:
                continue
            else:
                for job in parse_crontab(filename, withuser=False):
                    generate_timer_unit(job, seq=seqs.setdefault(job['j']+job['u'], count()))
        try:
            open(REBOOT_FILE,'a').close()
        except:
            pass
    else:
        # schedule rerun
        with open('%s/cron-after-var.service' % TARGET_DIR, 'w') as f:
            f.write('[Unit]\n')
            f.write('Description=Rerun systemd-crontab-generator because /var is a separate mount\n')
            f.write('Documentation=man:systemd.cron(7)\n')
            f.write('After=cron.target\n')
            f.write('ConditionDirectoryNotEmpty=@statedir@\n')

            f.write('\n[Service]\n')
            f.write('Type=oneshot\n')
            f.write('ExecStart=/bin/sh -c "systemctl daemon-reload ; systemctl try-restart cron.target"\n')

        MULTIUSER_DIR = os.path.join(TARGET_DIR, 'multi-user.target.wants')

        try:
           os.makedirs(MULTIUSER_DIR)
        except OSError as e:
           if e.errno != errno.EEXIST:
               raise

        try:
            os.symlink('%s/cron-after-var.service' % TARGET_DIR, '%s/cron-after-var.service' % MULTIUSER_DIR)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise


if __name__ == '__main__':
    if len(sys.argv) == 1 or not os.path.isdir(sys.argv[1]):
        sys.exit("Usage: %s <destination_folder>" % sys.argv[0])

    TARGET_DIR = sys.argv[1]
    TIMERS_DIR = os.path.join(TARGET_DIR, 'cron.target.wants')

    try:
        main()
    except Exception as e:
        if len(sys.argv) == 4:
            open('/dev/kmsg', 'w').write('<2> %s[%s]: global exception: %s\n' % (SELF, os.getpid(), e))
            exit(1)
        else:
            raise
