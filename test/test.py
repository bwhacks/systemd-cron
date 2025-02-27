#!/usr/bin/python3
import importlib
import unittest

# https://github.com/wntrblm/nox/pull/498

def m():
    loader = importlib.machinery.SourceFileLoader('name',
                'src/bin/systemd-crontab-generator.py')
    return loader.load_module()

class TestStringMethods(unittest.TestCase):

    def test_period_basic(self):
        j = m().Job('-', '@daily dummy true')
        j.parse_crontab_at(withuser=True)
        j.generate_schedule()
        self.assertEqual(j.schedule, 'daily')

    def test_timespec_basic(self):
        j = m().Job('-', '5 6 * * * dummy true')
        j.parse_crontab_timespec(withuser=True)
        j.generate_schedule()
        self.assertEqual(j.schedule, '*-*-* 6:5:00')

    def test_timespec_range(self):
        j = m().Job('-', '1 * * * mon-wed dummy true')
        j.parse_crontab_timespec(withuser=True)
        j.generate_schedule()
        self.assertEqual(j.schedule, 'Mon,Tue,Wed *-*-* *:1:00')

if __name__ == '__main__':
    unittest.main()
