"""Prevent inference from surviving its original owning server process."""
import os
import threading
import time


def guard_parent(parent_pid):
    if not isinstance(parent_pid,int) or parent_pid<=0 or parent_pid==os.getpid():
        raise RuntimeError('无法确定分轨任务所属服务')
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        # Retain the specific process handle, rather than checking a reusable PID.
        handle=kernel.OpenProcess(0x00100000,False,parent_pid)
        if not handle:
            raise RuntimeError('分轨所属服务已退出或不可访问')
        if kernel.WaitForSingleObject(handle,0)!=258:
            kernel.CloseHandle(handle)
            raise RuntimeError('分轨所属服务已退出')
        def watch():
            try:
                result=kernel.WaitForSingleObject(handle,0xFFFFFFFF)
                if result==0:
                    os._exit(75)
            finally:
                kernel.CloseHandle(handle)
    else:
        if os.getppid()!=parent_pid:
            raise RuntimeError('分轨所属服务已退出')
        def watch():
            while True:
                if os.getppid()!=parent_pid:
                    os._exit(75)
                time.sleep(.25)
    threading.Thread(target=watch,daemon=True,name='inference-parent-guard').start()
