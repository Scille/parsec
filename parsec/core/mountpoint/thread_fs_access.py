# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import trio


class ThreadFSAccess:
    def __init__(self, trio_token, workspace_fs):
        self.workspace_fs = workspace_fs
        self._trio_token = trio_token

    def _run(self, fn, *args):
        return trio.from_thread.run(fn, *args, trio_token=self._trio_token)

    def _run_sync(self, fn, *args):
        return trio.from_thread.run_sync(fn, *args, trio_token=self._trio_token)

    # Rights check

    def check_read_rights(self, path):
        return self._run_sync(self.workspace_fs.transactions.check_read_rights, path)

    def check_write_rights(self, path):
        return self._run_sync(self.workspace_fs.transactions.check_write_rights, path)

    # Entry transactions

    def entry_info(self, path):
        return self._run(self.workspace_fs.transactions.entry_info, path)

    def entry_rename(self, source, destination, *, overwrite):
        return self._run(
            self.workspace_fs.transactions.entry_rename, source, destination, overwrite
        )

    # Folder transactions

    def folder_create(self, path):
        return self._run(self.workspace_fs.transactions.folder_create, path)

    def folder_delete(self, path):
        return self._run(self.workspace_fs.transactions.folder_delete, path)

    # File transactions

    def file_create(self, path, *, open):
        return self._run(self.workspace_fs.transactions.file_create, path, open)

    def file_open(self, path, *, mode):
        return self._run(self.workspace_fs.transactions.file_open, path, mode)

    def file_delete(self, path):
        return self._run(self.workspace_fs.transactions.file_delete, path)

    def file_resize(self, path, length):
        return self._run(self.workspace_fs.transactions.file_resize, path, length)

    # File descriptor transactions

    def fd_close(self, fh):
        return self._run(self.workspace_fs.transactions.fd_close, fh)

    def fd_read(self, fh, size, offset, raise_eof=False):
        return self._run(self.workspace_fs.transactions.fd_read, fh, size, offset, raise_eof)

    def fd_write(self, fh, data, offset, constrained=False):
        return self._run(self.workspace_fs.transactions.fd_write, fh, data, offset, constrained)

    def fd_resize(self, fh, length, truncate_only=False):
        return self._run(self.workspace_fs.transactions.fd_resize, fh, length, truncate_only)

    def fd_flush(self, fh):
        return self._run(self.workspace_fs.transactions.fd_flush, fh)

    # Not-atomic stuff (for webdav fallback only)

    def move(self, source, destination):
        return self._run(self.workspace_fs.move, source, destination)

    def copytree(self, source, destination):
        return self._run(self.workspace_fs.copytree, source, destination)

    def copyfile(self, source, destination):
        return self._run(self.workspace_fs.copyfile, source, destination)
