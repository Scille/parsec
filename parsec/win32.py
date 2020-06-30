# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

from ctypes import FormatError, GetLastError, WinDLL
from ctypes.wintypes import BOOL, HANDLE, LPCWSTR, LPVOID

ERROR_ALREADY_EXISTS = 183


def SUCCEEDED(Status):
    return (Status) >= 0


def make_error(function, function_name=None):
    code = GetLastError()
    description = FormatError(code).strip()
    if function_name is None:
        function_name = function.__name__
    exception = WindowsError()
    exception.winerror = code
    exception.function = function_name
    exception.strerror = description
    return exception


def check_null(result, function, arguments, *args):
    if result is None:
        raise make_error(function)
    return result


def check_true(result, function, arguments, *args):
    if not result:
        raise make_error(function)
    return result


class Libraries(object):
    def __getattr__(self, name):
        library = WinDLL(name)
        self.__dict__[name] = library
        return library


dlls = Libraries()


def function_factory(function, argument_types=None, return_type=None, error_checking=None):
    if argument_types is not None:
        function.argtypes = argument_types
    function.restype = return_type
    if error_checking is not None:
        function.errcheck = error_checking
    return function


CreateMutex = function_factory(
    dlls.kernel32.CreateMutexW, [LPVOID, BOOL, LPCWSTR], HANDLE, check_null
)


CloseHandle = function_factory(dlls.kernel32.CloseHandle, [HANDLE], BOOL, check_true)
