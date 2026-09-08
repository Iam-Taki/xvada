import time
from lark import Lark
import tempfile
import subprocess
import os

"""
This file gives  classes to use as "Oracles" in the Arvada algorithm.
"""

class ParseException(Exception):
    pass

class ExternalOracle:
    """
    An ExternalOracle is a wrapper around an oracle that takes the form of a shell
    command accepting a file as input. We assume the oracle returns True if the
    exit code is 0 (no error). If the external oracle takes >3 seconds to execute,
    we conservatively assume the oracle returns True.
    """
    def __init__(self, command):
        self.command = command
        self.cache_set = {}
        self.parse_calls = 0
        self.real_calls = 0
        self.time_spent = 0
        if command == "liquid":
            from liquid import Environment
            self._environment_class = Environment

    def _parse_internal(self, string):
        self.real_calls += 1
        if self.command == "liquid":
            try:
                self._environment_class().from_string(string)
                return True
            except Exception:
                return False
        FNULL = open(os.devnull, 'w')
        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(bytes(string, 'utf-8'))
        f_name = f.name
        f.flush()
        f.close()
        try:
            subprocess.run(f'{self.command} "{f_name}"', shell=True, stdout=FNULL, stderr=FNULL, check=True)
            FNULL.close()
            os.remove(f_name)
            return True
        except subprocess.CalledProcessError as e:
            FNULL.close()
            os.remove(f_name)
            return False
        except subprocess.TimeoutExpired as e:
            print(f"Caused timeout: {string}")
            FNULL.close()
            os.remove(f_name)
            return True

    def parse(self, string, timeout=3):
        """
        Caching wrapper around _parse_internal
        """
        self.parse_calls += 1
        if string in self.cache_set:
            result = self.cache_set[string]
            self._log(string, result, cached=True)
            if result:
                return True
            else:
                raise ParseException(f"doesn't parse: {string}")
        else:
            s = time.time()
            res = self._parse_internal(string)
            self.time_spent += time.time() - s
            self.cache_set[string] = res
            self._log(string, res, cached=False)
            if res:
                return True
            else:
                raise ParseException(f"doesn't parse: {string}")

    def _log(self, string, result, cached):
        try:
            with open("oracle_log.csv", "a", encoding="utf-8") as logf:
                preview = string[:50].replace("\n", " ").replace("|", "_")
                logf.write(f"{self.parse_calls}|{len(string)}|{result}|{cached}|{preview}\n")
        except Exception:
            pass

class CachingOracle:
    """
    Wraps a "Lark" parser object to provide caching of previous calls.
    """
    def __init__(self, oracle: Lark):
        self.oracle = oracle
        self.cache_set = {}
        self.parse_calls = 0

    def parse(self, string):
        self.parse_calls += 1
        if string in self.cache_set:
            if self.cache_set[string]:
                return True
            else:
                raise ParseException("doesn't parse")
        else:
            try:
                self.oracle.parse(string)
                self.cache_set[string] = True
            except Exception as e:
                self.cache_set[string] = False
                raise ParseException("doesn't parse")