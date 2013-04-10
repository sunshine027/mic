#!/usr/bin/python -tt
#
# Copyright (c) 2013 Intel, Inc.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; version 2 of the License
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc., 59
# Temple Place - Suite 330, Boston, MA 02111-1307, USA.

""" This module implements a simple GPT partitions parser which can read the
GPT header and the GPT partition table. """

import struct
import uuid
import binascii
from mic.utils.errors import MountError

_GPT_HEADER_FORMAT = "<8s4sIIIQQQQ16sQIII"
_GPT_HEADER_SIZE = struct.calcsize(_GPT_HEADER_FORMAT)
_GPT_ENTRY_FORMAT = "<16s16sQQQ72s"
_GPT_ENTRY_SIZE = struct.calcsize(_GPT_ENTRY_FORMAT)
_SUPPORTED_GPT_REVISION = '\x00\x00\x01\x00'

def _stringify_uuid(binary_uuid):
    """ A small helper function to transform a binary UUID into a string
    format. """

    uuid_str = str(uuid.UUID(bytes_le = binary_uuid))

    return uuid_str.upper()

def _calc_header_crc(raw_hdr):
    """ Calculate GPT header CRC32 checksum. The 'raw_hdr' parameter has to
    be a list or a tuple containing all the elements of the GPT header in a
    "raw" form, meaning that it should simply contain "unpacked" disk data.
    """

    raw_hdr = list(raw_hdr)
    raw_hdr[3] = 0
    raw_hdr = struct.pack(_GPT_HEADER_FORMAT, *raw_hdr)

    return binascii.crc32(raw_hdr) & 0xFFFFFFFF

def _validate_header(raw_hdr):
    """ Validate the GPT header. The 'raw_hdr' parameter has to be a list or a
    tuple containing all the elements of the GPT header in a "raw" form,
    meaning that it should simply contain "unpacked" disk data. """

    # Validate the signature
    if raw_hdr[0] != 'EFI PART':
        raise MountError("GPT partition table not found")

    # Validate the revision
    if raw_hdr[1] != _SUPPORTED_GPT_REVISION:
        raise MountError("Unsupported GPT revision '%s', supported revision " \
                         "is '%s'" % \
                          (binascii.hexlify(raw_hdr[1]),
                           binascii.hexlify(_SUPPORTED_GPT_REVISION)))

    # Validate header size
    if raw_hdr[2] != _GPT_HEADER_SIZE:
        raise MountError("Bad GPT header size: %d bytes, expected %d" % \
                         (raw_hdr[2], _GPT_HEADER_SIZE))

    crc = _calc_header_crc(raw_hdr)
    if raw_hdr[3] != crc:
        raise MountError("GPT header crc mismatch: %#x, should be %#x" % \
                         (crc, raw_hdr[3]))

class GptParser:
    """ GPT partition table parser. The current implementation is simplified
    and it assumes that the partition table is correct, so it does not check
    the CRC-32 checksums and does not handle the backup GPT partition table.
    But this implementation can be extended in the future, if needed. """

    def __init__(self, disk_path, sector_size = 512):
        """ The class constructor which accepts the following parameters:
            * disk_path - full path to the disk image or device node
            * sector_size - size of a disk sector in bytes """

        self.sector_size = sector_size
        self.disk_path = disk_path

        try:
            self._disk_obj = open(disk_path, 'rb')
        except IOError as err:
            raise MountError("Cannot open file '%s' for reading GPT " \
                             "partitions: %s" % (disk_path, err))

    def __del__(self):
        """ The class destructor. """

        self._disk_obj.close()

    def _read_disk(self, offset, size):
        """ A helper function which reads 'size' bytes from offset 'offset' of
        the disk and checks all the error conditions. """

        self._disk_obj.seek(offset)
        try:
            data = self._disk_obj.read(size)
        except IOError as err:
            raise MountError("cannot read from '%s': %s" % \
                             (self.disk_path, err))

        if len(data) != size:
            raise MountError("cannot read %d bytes from offset '%d' of '%s', " \
                             "read only %d bytes" % \
                             (size, offset, self.disk_path, len(data)))

        return data

    def read_header(self, primary = True):
        """ Read and verify the GPT header and return a tuple containing the
        following elements:

        (Signature, Revision, Header size in bytes, header CRC32, Current LBA,
        Backup LBA, First usable LBA for partitions, Last usable LBA, Disk GUID,
        Starting LBA of array of partition entries, Number of partition entries,
        Size of a single partition entry, CRC32 of partition array)

        This tuple corresponds to the GPT header format. Please, see the UEFI
        standard for the description of these fields.

        If the 'primary' parameter is 'True', the primary GPT header is read,
        otherwise the backup GPT header is read instead. """

        # Read and validate the primary GPT header
        raw_hdr = self._read_disk(self.sector_size, _GPT_HEADER_SIZE)
        raw_hdr = struct.unpack(_GPT_HEADER_FORMAT, raw_hdr)
        _validate_header(raw_hdr)

        if not primary:
            raw_hdr = self._read_disk(raw_hdr[6] * self.sector_size, _GPT_HEADER_SIZE)
            raw_hdr = struct.unpack(_GPT_HEADER_FORMAT, raw_hdr)
            _validate_header(raw_hdr)

        return (raw_hdr[0], # 0. Signature
                raw_hdr[1], # 1. Revision
                raw_hdr[2], # 2. Header size in bytes
                raw_hdr[3], # 3. Header CRC32
                raw_hdr[5], # 4. Current LBA
                raw_hdr[6], # 5. Backup LBA
                raw_hdr[7], # 6. First usable LBA for partitions
                raw_hdr[8], # 7. Last usable LBA
                _stringify_uuid(raw_hdr[9]), # 8. Disk GUID
                raw_hdr[10], # 9. Starting LBA of array of partition entries
                raw_hdr[11], # 10. Number of partition entries
                raw_hdr[12], # 11. Size of a single partition entry
                raw_hdr[13]) # 12. CRC32 of partition array

    def get_partitions(self, primary = True):
        """ This is a generator which parses the GPT partition table and
        generates the following tuples for each partition:

        (Index, Partition type GUID, Partition GUID, First LBA, Last LBA,
        Attribute flags, Partition name)

        All the elements in the tuple except 'Index' correspond to the GPT
        partition record format. Please, see the UEFI standard for the
        description of these fields. The 'Index' element is simply the index
        number of this entry in the partition table.

        If the 'primary' parameter is 'True', partitions from the primary GPT
        partition table are generated, otherwise partitions from the backup GPT
        partition table are generated. """

        header = self.read_header(primary)

        start = header[9] * self.sector_size
        index = -1

        for _ in xrange(0, header[10]):
            entry = self._read_disk(start, _GPT_ENTRY_SIZE)
            entry = struct.unpack(_GPT_ENTRY_FORMAT, entry)

            start += header[11]
            index += 1

            if entry[2] == 0 or entry[3] == 0:
                continue

            part_name = str(entry[5].decode('UTF-16').split('\0', 1)[0])

            yield (index,                     # 0. Partition table entry number
                   _stringify_uuid(entry[0]), # 1. Partition type GUID
                   _stringify_uuid(entry[1]), # 2. Partition GUID
                   entry[2],                  # 3. First LBA
                   entry[3],                  # 4. Last LBA
                   entry[4],                  # 5. Attribute flags
                   part_name)                 # 6. Partition name
