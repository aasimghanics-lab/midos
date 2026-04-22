# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# PageInfo: metadata for each physical page frame (valid, dirty, LRU tracking).
# SwapManager: handles reading/writing pages to/from a swap file on disk.

import os
import time


class PageInfo:
    """
    Metadata for a single physical page frame.

    Attributes:
        is_valid:   True if the page is currently loaded in physical memory.
        is_dirty:   True if the page has been written to since it was loaded.
        owner_pid:  PID of the process that owns this page (None if free).
        vpage:      The virtual page number this frame currently holds.
        last_access: Monotonic counter value for LRU tracking.
    """

    def __init__(self):
        self.is_valid    = True    # starts valid (in physical memory)
        self.is_dirty    = False   # clean until written
        self.owner_pid   = None    # no owner yet
        self.vpage       = None    # which virtual page is stored here
        self.last_access = 0       # LRU counter

    def __repr__(self):
        status = "V" if self.is_valid else "I"
        dirty  = "D" if self.is_dirty else "C"
        owner  = f"PID{self.owner_pid}" if self.owner_pid else "free"
        return (f"Page({status}/{dirty}, vp={self.vpage}, "
                f"{owner}, lru={self.last_access})")


class SwapManager:
    """
    Manages a swap file on disk for page eviction/restoration.

    Each evicted page is stored as a raw block of page_size bytes
    keyed by (pid, virtual_page_number). The swap file is a simple
    binary file with an in-memory index mapping (pid, vpage) to
    file offsets.
    """

    def __init__(self, swap_filename="midos_swap.bin", page_size=256):
        self.swap_filename = swap_filename
        self.page_size     = page_size

        # Index: (pid, vpage) -> offset in swap file
        self._index = {}

        # Next write offset
        self._next_offset = 0

        # Free list of offsets (from pages swapped back in that can be reused)
        self._free_offsets = []

        # Create or truncate the swap file
        with open(self.swap_filename, 'wb') as f:
            pass  # empty file

    def write_page(self, pid, vpage, data):
        """
        Write a page's data to the swap file.

        Args:
            pid:   process ID
            vpage: virtual page number
            data:  bytes/bytearray of length page_size

        Returns:
            offset where the page was written
        """
        assert len(data) == self.page_size, \
            f"Expected {self.page_size} bytes, got {len(data)}"

        key = (pid, vpage)

        # Reuse existing slot if this page was already swapped
        if key in self._index:
            offset = self._index[key]
        elif self._free_offsets:
            offset = self._free_offsets.pop()
            self._index[key] = offset
        else:
            offset = self._next_offset
            self._next_offset += self.page_size
            self._index[key] = offset

        with open(self.swap_filename, 'r+b') as f:
            f.seek(offset)
            f.write(data)

        return offset

    def read_page(self, pid, vpage):
        """
        Read a page's data from the swap file.

        Args:
            pid:   process ID
            vpage: virtual page number

        Returns:
            bytearray of length page_size

        Raises:
            KeyError if the page is not in the swap file.
        """
        key = (pid, vpage)
        if key not in self._index:
            raise KeyError(f"Page (PID={pid}, vpage={vpage}) not in swap")

        offset = self._index[key]
        with open(self.swap_filename, 'rb') as f:
            f.seek(offset)
            data = f.read(self.page_size)

        return bytearray(data)

    def has_page(self, pid, vpage):
        """Check if a page exists in swap."""
        return (pid, vpage) in self._index

    def remove_page(self, pid, vpage):
        """
        Remove a page from the swap index (e.g., on process exit).
        The slot is added to the free list for reuse.
        """
        key = (pid, vpage)
        if key in self._index:
            self._free_offsets.append(self._index[key])
            del self._index[key]

    def remove_all_for_process(self, pid):
        """Remove all swap entries for a given process."""
        keys_to_remove = [k for k in self._index if k[0] == pid]
        for key in keys_to_remove:
            self._free_offsets.append(self._index[key])
            del self._index[key]

    def cleanup(self):
        """Delete the swap file."""
        try:
            os.remove(self.swap_filename)
        except OSError:
            pass

    def stats(self):
        """Return swap usage statistics."""
        return {
            'pages_in_swap': len(self._index),
            'free_slots':    len(self._free_offsets),
            'file_size':     self._next_offset,
        }
