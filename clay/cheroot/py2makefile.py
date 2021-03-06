import socket

class FauxSocket(object):
    """Faux socket with the minimal interface required by pypy"""

    def _reuse(self):
        pass

_fileobject_uses_str_type = isinstance(socket._fileobject(FauxSocket())._rbuf, basestring)

from ._compat import StringIO
from . import errors


class makefile(socket._fileobject):
    """Faux file object attached to a socket object."""

    def __init__(self, *args, **kwargs):
        self.bytes_read = 0
        self.bytes_written = 0
        socket._fileobject.__init__(self, *args, **kwargs)

    def sendall(self, data):
        """Sendall for non-blocking sockets."""
        while data:
            try:
                bytes_sent = self.send(data)
                data = data[bytes_sent:]
            except socket.error, e:
                if e.args[0] not in errors.socket_errors_nonblocking:
                    raise

    def send(self, data):
        bytes_sent = self._sock.send(data)
        self.bytes_written += bytes_sent
        return bytes_sent

    def flush(self):
        if self._wbuf:
            buffer = "".join(self._wbuf)
            self._wbuf = []
            self.sendall(buffer)

    def recv(self, size):
        while True:
            try:
                data = self._sock.recv(size)
                self.bytes_read += len(data)
                return data
            except socket.error, e:
                if (e.args[0] not in errors.socket_errors_nonblocking
                    and e.args[0] not in errors.socket_error_eintr):
                    raise

    if not _fileobject_uses_str_type:
        def read(self, size=-1):
            # Use max, disallow tiny reads in a loop as they are very inefficient.
            # We never leave read() with any leftover data from a new recv() call
            # in our internal buffer.
            rbufsize = max(self._rbufsize, self.default_bufsize)
            # Our use of StringIO rather than lists of string objects returned by
            # recv() minimizes memory usage and fragmentation that occurs when
            # rbufsize is large compared to the typical return value of recv().
            buf = self._rbuf
            buf.seek(0, 2)  # seek end
            if size < 0:
                # Read until EOF
                self._rbuf = StringIO()  # reset _rbuf.  we consume it via buf.
                while True:
                    data = self.recv(rbufsize)
                    if not data:
                        break
                    buf.write(data)
                return buf.getvalue()
            else:
                # Read until size bytes or EOF seen, whichever comes first
                buf_len = buf.tell()
                if buf_len >= size:
                    # Already have size bytes in our buffer?  Extract and return.
                    buf.seek(0)
                    rv = buf.read(size)
                    self._rbuf = StringIO()
                    self._rbuf.write(buf.read())
                    return rv

                self._rbuf = StringIO()  # reset _rbuf.  we consume it via buf.
                while True:
                    left = size - buf_len
                    # recv() will malloc the amount of memory given as its
                    # parameter even though it often returns much less data
                    # than that.  The returned data string is short lived
                    # as we copy it into a StringIO and free it.  This avoids
                    # fragmentation issues on many platforms.
                    data = self.recv(left)
                    if not data:
                        break
                    n = len(data)
                    if n == size and not buf_len:
                        # Shortcut.  Avoid buffer data copies when:
                        # - We have no data in our buffer.
                        # AND
                        # - Our call to recv returned exactly the
                        #   number of bytes we were asked to read.
                        return data
                    if n == left:
                        buf.write(data)
                        del data  # explicit free
                        break
                    assert n <= left, "recv(%d) returned %d bytes" % (left, n)
                    buf.write(data)
                    buf_len += n
                    del data  # explicit free
                    #assert buf_len == buf.tell()
                return buf.getvalue()

        def readline(self, size=-1):
            buf = self._rbuf
            buf.seek(0, 2)  # seek end
            if buf.tell() > 0:
                # check if we already have it in our buffer
                buf.seek(0)
                bline = buf.readline(size)
                if bline.endswith('\n') or len(bline) == size:
                    self._rbuf = StringIO()
                    self._rbuf.write(buf.read())
                    return bline
                del bline
            if size < 0:
                # Read until \n or EOF, whichever comes first
                if self._rbufsize <= 1:
                    # Speed up unbuffered case
                    buf.seek(0)
                    buffers = [buf.read()]
                    self._rbuf = StringIO()  # reset _rbuf.  we consume it via buf.
                    data = None
                    recv = self.recv
                    while data != "\n":
                        data = recv(1)
                        if not data:
                            break
                        buffers.append(data)
                    return "".join(buffers)

                buf.seek(0, 2)  # seek end
                self._rbuf = StringIO()  # reset _rbuf.  we consume it via buf.
                while True:
                    data = self.recv(self._rbufsize)
                    if not data:
                        break
                    nl = data.find('\n')
                    if nl >= 0:
                        nl += 1
                        buf.write(data[:nl])
                        self._rbuf.write(data[nl:])
                        del data
                        break
                    buf.write(data)
                return buf.getvalue()
            else:
                # Read until size bytes or \n or EOF seen, whichever comes first
                buf.seek(0, 2)  # seek end
                buf_len = buf.tell()
                if buf_len >= size:
                    buf.seek(0)
                    rv = buf.read(size)
                    self._rbuf = StringIO()
                    self._rbuf.write(buf.read())
                    return rv
                self._rbuf = StringIO()  # reset _rbuf.  we consume it via buf.
                while True:
                    data = self.recv(self._rbufsize)
                    if not data:
                        break
                    left = size - buf_len
                    # did we just receive a newline?
                    nl = data.find('\n', 0, left)
                    if nl >= 0:
                        nl += 1
                        # save the excess data to _rbuf
                        self._rbuf.write(data[nl:])
                        if buf_len:
                            buf.write(data[:nl])
                            break
                        else:
                            # Shortcut.  Avoid data copy through buf when returning
                            # a substring of our first recv().
                            return data[:nl]
                    n = len(data)
                    if n == size and not buf_len:
                        # Shortcut.  Avoid data copy through buf when
                        # returning exactly all of our first recv().
                        return data
                    if n >= left:
                        buf.write(data[:left])
                        self._rbuf.write(data[left:])
                        break
                    buf.write(data)
                    buf_len += n
                    #assert buf_len == buf.tell()
                return buf.getvalue()
    else:
        def read(self, size=-1):
            if size < 0:
                # Read until EOF
                buffers = [self._rbuf]
                self._rbuf = ""
                if self._rbufsize <= 1:
                    recv_size = self.default_bufsize
                else:
                    recv_size = self._rbufsize

                while True:
                    data = self.recv(recv_size)
                    if not data:
                        break
                    buffers.append(data)
                return "".join(buffers)
            else:
                # Read until size bytes or EOF seen, whichever comes first
                data = self._rbuf
                buf_len = len(data)
                if buf_len >= size:
                    self._rbuf = data[size:]
                    return data[:size]
                buffers = []
                if data:
                    buffers.append(data)
                self._rbuf = ""
                while True:
                    left = size - buf_len
                    recv_size = max(self._rbufsize, left)
                    data = self.recv(recv_size)
                    if not data:
                        break
                    buffers.append(data)
                    n = len(data)
                    if n >= left:
                        self._rbuf = data[left:]
                        buffers[-1] = data[:left]
                        break
                    buf_len += n
                return "".join(buffers)

        def readline(self, size=-1):
            data = self._rbuf
            if size < 0:
                # Read until \n or EOF, whichever comes first
                if self._rbufsize <= 1:
                    # Speed up unbuffered case
                    assert data == ""
                    buffers = []
                    while data != "\n":
                        data = self.recv(1)
                        if not data:
                            break
                        buffers.append(data)
                    return "".join(buffers)
                nl = data.find('\n')
                if nl >= 0:
                    nl += 1
                    self._rbuf = data[nl:]
                    return data[:nl]
                buffers = []
                if data:
                    buffers.append(data)
                self._rbuf = ""
                while True:
                    data = self.recv(self._rbufsize)
                    if not data:
                        break
                    buffers.append(data)
                    nl = data.find('\n')
                    if nl >= 0:
                        nl += 1
                        self._rbuf = data[nl:]
                        buffers[-1] = data[:nl]
                        break
                return "".join(buffers)
            else:
                # Read until size bytes or \n or EOF seen, whichever comes first
                nl = data.find('\n', 0, size)
                if nl >= 0:
                    nl += 1
                    self._rbuf = data[nl:]
                    return data[:nl]
                buf_len = len(data)
                if buf_len >= size:
                    self._rbuf = data[size:]
                    return data[:size]
                buffers = []
                if data:
                    buffers.append(data)
                self._rbuf = ""
                while True:
                    data = self.recv(self._rbufsize)
                    if not data:
                        break
                    buffers.append(data)
                    left = size - buf_len
                    nl = data.find('\n', 0, left)
                    if nl >= 0:
                        nl += 1
                        self._rbuf = data[nl:]
                        buffers[-1] = data[:nl]
                        break
                    n = len(data)
                    if n >= left:
                        self._rbuf = data[left:]
                        buffers[-1] = data[:left]
                        break
                    buf_len += n
                return "".join(buffers)

