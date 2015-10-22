#!/usr/bin/env python
#
# Copyright 2015-2015 breakwa11
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from __future__ import absolute_import, division, print_function, \
    with_statement

import os
import sys
import hashlib
import logging
import binascii
import base64
import time
import datetime
import random
import struct
import zlib

import shadowsocks
from shadowsocks import common
from shadowsocks.obfsplugin import plain
from shadowsocks.common import to_bytes, to_str, ord

def create_verify_obfs(method):
    return verify_simple(method)

def create_verify_deflate(method):
    return verify_deflate(method)

def create_auth_obfs(method):
    return auth_simple(method)

obfs_map = {
        'verify_simple': (create_verify_obfs,),
        'verify_deflate': (create_verify_deflate,),
        'auth_simple': (create_auth_obfs,),
}

def match_begin(str1, str2):
    if len(str1) >= len(str2):
        if str1[:len(str2)] == str2:
            return True
    return False

class obfs_verify_data(object):
    def __init__(self):
        self.sub_obfs = None

class verify_base(plain.plain):
    def __init__(self, method):
        super(verify_base, self).__init__(method)
        self.method = method
        self.sub_obfs = None

    def init_data(self):
        return obfs_verify_data()

    def set_server_info(self, server_info):
        try:
            if server_info.param:
                sub_param = ''
                param_list = server_info.param.split(',', 1)
                if len(param_list) > 1:
                    self.sub_obfs = shadowsocks.obfs.obfs(param_list[0])
                    sub_param = param_list[1]
                else:
                    self.sub_obfs = shadowsocks.obfs.obfs(server_info.param)
                if server_info.data.sub_obfs is None:
                    server_info.data.sub_obfs = self.sub_obfs.init_data()
                _server_info = shadowsocks.obfs.server_info(server_info.data.sub_obfs)
                _server_info.host = server_info.host
                _server_info.port = server_info.port
                _server_info.tcp_mss = server_info.tcp_mss
                _server_info.param = sub_param
                self.sub_obfs.set_server_info(_server_info)
        except Exception as e:
            shadowsocks.shell.print_exception(e)
        self.server_info = server_info

    def client_encode(self, buf):
        if self.sub_obfs is not None:
            return self.sub_obfs.client_encode(buf)
        return buf

    def client_decode(self, buf):
        if self.sub_obfs is not None:
            return self.sub_obfs.client_decode(buf)
        return (buf, False)

    def server_encode(self, buf):
        if self.sub_obfs is not None:
            return self.sub_obfs.server_encode(buf)
        return buf

    def server_decode(self, buf):
        if self.sub_obfs is not None:
            return self.sub_obfs.server_decode(buf)
        return (buf, True, False)

class verify_simple(verify_base):
    def __init__(self, method):
        super(verify_simple, self).__init__(method)
        self.recv_buf = b''
        self.unit_len = 8100
        self.decrypt_packet_num = 0
        self.raw_trans = False

    def pack_data(self, buf):
        if len(buf) == 0:
            return b''
        rnd_data = os.urandom(common.ord(os.urandom(1)[0]) % 16)
        data = common.chr(len(rnd_data) + 1) + rnd_data + buf
        data = struct.pack('>H', len(data) + 6) + data
        crc = (0xffffffff - binascii.crc32(data)) & 0xffffffff
        data += struct.pack('<I', crc)
        return data

    def client_pre_encrypt(self, buf):
        ret = b''
        while len(buf) > self.unit_len:
            ret += self.pack_data(buf[:self.unit_len])
            buf = buf[self.unit_len:]
        ret += self.pack_data(buf)
        return ret

    def client_post_decrypt(self, buf):
        if self.raw_trans:
            return buf
        self.recv_buf += buf
        out_buf = b''
        while len(self.recv_buf) > 2:
            length = struct.unpack('>H', self.recv_buf[:2])[0]
            if length >= 8192:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return None
                else:
                    raise Exception('server_post_decrype data error')
            if length > len(self.recv_buf):
                break

            if (binascii.crc32(self.recv_buf[:length]) & 0xffffffff) != 0xffffffff:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return None
                else:
                    raise Exception('server_post_decrype data uncorrect CRC32')

            pos = common.ord(self.recv_buf[2]) + 2
            out_buf += self.recv_buf[pos:length - 4]
            self.recv_buf = self.recv_buf[length:]

        if out_buf:
            self.decrypt_packet_num += 1
        return out_buf

    def server_pre_encrypt(self, buf):
        ret = b''
        while len(buf) > self.unit_len:
            ret += self.pack_data(buf[:self.unit_len])
            buf = buf[self.unit_len:]
        ret += self.pack_data(buf)
        return ret

    def server_post_decrypt(self, buf):
        if self.raw_trans:
            return buf
        self.recv_buf += buf
        out_buf = b''
        while len(self.recv_buf) > 2:
            length = struct.unpack('>H', self.recv_buf[:2])[0]
            if length >= 8192:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return b'E'
                else:
                    raise Exception('server_post_decrype data error')
            if length > len(self.recv_buf):
                break

            if (binascii.crc32(self.recv_buf[:length]) & 0xffffffff) != 0xffffffff:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return b'E'
                else:
                    raise Exception('server_post_decrype data uncorrect CRC32')

            pos = common.ord(self.recv_buf[2]) + 2
            out_buf += self.recv_buf[pos:length - 4]
            self.recv_buf = self.recv_buf[length:]

        if out_buf:
            self.decrypt_packet_num += 1
        return out_buf

class verify_deflate(verify_base):
    def __init__(self, method):
        super(verify_deflate, self).__init__(method)
        self.recv_buf = b''
        self.unit_len = 32700
        self.decrypt_packet_num = 0
        self.raw_trans = False

    def pack_data(self, buf):
        if len(buf) == 0:
            return b''
        data = zlib.compress(buf)
        data = struct.pack('>H', len(data)) + data[2:]
        return data

    def client_pre_encrypt(self, buf):
        ret = b''
        while len(buf) > self.unit_len:
            ret += self.pack_data(buf[:self.unit_len])
            buf = buf[self.unit_len:]
        ret += self.pack_data(buf)
        return ret

    def client_post_decrypt(self, buf):
        if self.raw_trans:
            return buf
        self.recv_buf += buf
        out_buf = b''
        while len(self.recv_buf) > 2:
            length = struct.unpack('>H', self.recv_buf[:2])[0]
            if length >= 32768:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return None
                else:
                    raise Exception('server_post_decrype data error')
            if length > len(self.recv_buf):
                break

            out_buf += zlib.decompress(b'x\x9c' + self.recv_buf[2:length])
            self.recv_buf = self.recv_buf[length:]

        if out_buf:
            self.decrypt_packet_num += 1
        return out_buf

    def server_pre_encrypt(self, buf):
        ret = b''
        while len(buf) > self.unit_len:
            ret += self.pack_data(buf[:self.unit_len])
            buf = buf[self.unit_len:]
        ret += self.pack_data(buf)
        return ret

    def server_post_decrypt(self, buf):
        if self.raw_trans:
            return buf
        self.recv_buf += buf
        out_buf = b''
        while len(self.recv_buf) > 2:
            length = struct.unpack('>H', self.recv_buf[:2])[0]
            if length >= 32768:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return None
                else:
                    raise Exception('server_post_decrype data error')
            if length > len(self.recv_buf):
                break

            out_buf += zlib.decompress(b'\x78\x9c' + self.recv_buf[2:length])
            self.recv_buf = self.recv_buf[length:]

        if out_buf:
            self.decrypt_packet_num += 1
        return out_buf

class client_queue(object):
    def __init__(self, begin_id):
        self.front = begin_id
        self.back = begin_id
        self.alloc = {}
        self.enable = True
        self.last_update = time.time()

    def update(self):
        self.last_update = time.time()

    def is_active(self):
        return time.time() - self.last_update < 60 * 3

    def re_enable(self, connection_id):
        self.enable = True
        self.alloc = {}
        self.front = connection_id
        self.back = connection_id

    def insert(self, connection_id):
        self.update()
        if not self.enable:
            return False
        if connection_id < self.front:
            return False
        if not self.is_active():
            self.re_enable(connection_id)
        if connection_id > self.front + 0x4000:
            return False
        if connection_id in self.alloc:
            return False
        if self.back <= connection_id:
            self.back = connection_id + 1
        self.alloc[connection_id] = 1
        while (self.front in self.alloc) or self.front + 0x1000 < self.back:
            if self.front in self.alloc:
                del self.alloc[self.front]
            self.front += 1
        return True

class obfs_auth_data(object):
    def __init__(self):
        self.sub_obfs = None
        self.client_id = {}

    def update(self, client_id, connection_id):
        if client_id in self.client_id:
            self.client_id[client_id].update()

    def insert(self, client_id, connection_id):
        max_client = 16
        if client_id not in self.client_id or not self.client_id[client_id].enable:
            active = 0
            for c_id in self.client_id:
                if self.client_id[c_id].is_active():
                    active += 1
            if active >= max_client:
                return False

            if len(self.client_id) < max_client:
                if client_id not in self.client_id:
                    self.client_id[client_id] = client_queue(connection_id)
                else:
                    self.client_id[client_id].re_enable(connection_id)
                return self.client_id[client_id].insert(connection_id)
            keys = self.client_id.keys()
            random.shuffle(keys)
            for c_id in keys:
                if not self.client_id[c_id].is_active() and self.client_id[c_id].enable:
                    if len(self.client_id) >= 256:
                        del self.client_id[c_id]
                    else:
                        self.client_id[c_id].enable = False
                    if client_id not in self.client_id:
                        self.client_id[client_id] = client_queue(connection_id)
                    else:
                        self.client_id[client_id].re_enable(connection_id)
                    return self.client_id[client_id].insert(connection_id)
            return False
        else:
            return self.client_id[client_id].insert(connection_id)

class auth_simple(verify_base):
    def __init__(self, method):
        super(auth_simple, self).__init__(method)
        self.recv_buf = b''
        self.unit_len = 8100
        self.decrypt_packet_num = 0
        self.raw_trans = False
        self.has_sent_header = False
        self.has_recv_header = False
        self.client_id = 0
        self.connection_id = 0

    def init_data(self):
        return obfs_auth_data()

    def pack_data(self, buf):
        if len(buf) == 0:
            return b''
        rnd_data = os.urandom(common.ord(os.urandom(1)[0]) % 16)
        data = common.chr(len(rnd_data) + 1) + rnd_data + buf
        data = struct.pack('>H', len(data) + 6) + data
        crc = (0xffffffff - binascii.crc32(data)) & 0xffffffff
        data += struct.pack('<I', crc)
        return data

    def client_pre_encrypt(self, buf):
        ret = b''
        while len(buf) > self.unit_len:
            ret += self.pack_data(buf[:self.unit_len])
            buf = buf[self.unit_len:]
        ret += self.pack_data(buf)
        return ret

    def client_post_decrypt(self, buf):
        if self.raw_trans:
            return buf
        self.recv_buf += buf
        out_buf = b''
        while len(self.recv_buf) > 2:
            length = struct.unpack('>H', self.recv_buf[:2])[0]
            if length >= 8192:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return None
                else:
                    raise Exception('server_post_decrype data error')
            if length > len(self.recv_buf):
                break

            if (binascii.crc32(self.recv_buf[:length]) & 0xffffffff) != 0xffffffff:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return None
                else:
                    raise Exception('server_post_decrype data uncorrect CRC32')

            pos = common.ord(self.recv_buf[2]) + 2
            out_buf += self.recv_buf[pos:length - 4]
            self.recv_buf = self.recv_buf[length:]

        if out_buf:
            self.decrypt_packet_num += 1
        return out_buf

    def server_pre_encrypt(self, buf):
        ret = b''
        while len(buf) > self.unit_len:
            ret += self.pack_data(buf[:self.unit_len])
            buf = buf[self.unit_len:]
        ret += self.pack_data(buf)
        return ret

    def server_post_decrypt(self, buf):
        if self.raw_trans:
            return buf
        self.recv_buf += buf
        out_buf = b''
        while len(self.recv_buf) > 2:
            length = struct.unpack('>H', self.recv_buf[:2])[0]
            if length >= 8192:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return b'E'
                else:
                    raise Exception('server_post_decrype data error')
            if length > len(self.recv_buf):
                break

            if (binascii.crc32(self.recv_buf[:length]) & 0xffffffff) != 0xffffffff:
                self.raw_trans = True
                self.recv_buf = b''
                if self.decrypt_packet_num == 0:
                    return b'E'
                else:
                    raise Exception('server_post_decrype data uncorrect CRC32')

            pos = common.ord(self.recv_buf[2]) + 2
            out_buf += self.recv_buf[pos:length - 4]
            if not self.has_recv_header:
                if len(out_buf) < 8:
                    self.raw_trans = True
                    self.recv_buf = b''
                    return b'E'
                client_id = struct.unpack('<I', out_buf[:4])[0]
                connection_id = struct.unpack('<I', out_buf[4:8])[0]
                if self.server_info.data.insert(client_id, connection_id):
                    self.has_recv_header = True
                    out_buf = out_buf[8:]
                    self.client_id = client_id
                    self.connection_id = connection_id
                else:
                    self.raw_trans = True
                    self.recv_buf = b''
                    return b'E'
            self.recv_buf = self.recv_buf[length:]

        if out_buf:
            self.server_info.data.update(self.client_id, self.connection_id)
            self.decrypt_packet_num += 1
        return out_buf

