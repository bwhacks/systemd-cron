#!/usr/bin/python3
import glob
import time
import os

actual_stamps = glob.glob('/var/lib/systemd/timers/stamp-cron-*.timer')

timers = glob.glob('/run/systemd/generator/cron-*.timer')
needed_stamps = ['/var/lib/systemd/timers/stamp-cron-daily.timer',
                 '/var/lib/systemd/timers/stamp-cron-weekly.timer',
                 '/var/lib/systemd/timers/stamp-cron-monthly.timer',
                 '/var/lib/systemd/timers/stamp-cron-quarterly.timer',
                 '/var/lib/systemd/timers/stamp-cron-semi-annually.timer',
                 '/var/lib/systemd/timers/stamp-cron-yearly.timer']

for timer in timers:
    needed_stamps.append(timer.replace('/run/systemd/generator/cron-',
                                       '/var/lib/systemd/timers/stamp-cron-'))

stale_stamps = set(actual_stamps) - set(needed_stamps)

now = time.time()
for stale_stamp in stale_stamps:
    if os.stat(stale_stamp).st_mtime < now - 10 * 86400:
        try:
            os.remove(stale_stamp)
        except IOError:
            pass
