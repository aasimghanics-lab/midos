# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# HeapAllocator: first-fit allocator with dynamic heap growth.
#
# Module 5 enhancements:
#   - Maintains a separate table of heap pages.
#   - Supports allocations larger than a single page (broken across pages).
#   - Dynamically requests new pages from the PMM when the heap is exhausted.
#   - Coalesces adjacent free blocks to reduce fragmentation.
#   - Tracks all in-use allocations for proper freeing.


class HeapBlock:
    """Metadata for a single heap allocation or free region."""

    def __init__(self, start, size, free=True):
        self.start = start   # virtual address within the heap
        self.size  = size    # bytes
        self.free  = free

    def end(self):
        return self.start + self.size

    def __repr__(self):
        status = "free" if self.free else "used"
        return f"Block(0x{self.start:04X}-0x{self.end():04X}, {self.size}B, {status})"


class HeapAllocator:
    """
    Per-process first-fit heap allocator with dynamic growth.

    The process's heap starts at virtual address heap_start with an initial
    size of heap_size bytes.  When existing free space cannot satisfy a
    request, the allocator asks the PhysicalMemoryManager for additional
    pages, maps them contiguously at the top of the current heap, and
    extends the free list.

    Key Module 5 features:
        1. Separate table of heap pages (self.heap_pages).
        2. Allocations larger than one page are supported — the allocator
           will request enough contiguous virtual pages to cover the size.
        3. Adjacent free blocks are coalesced after every free() to
           maximise available contiguous space.
        4. If contiguous heap memory cannot be found, the allocation fails
           (returns None / sets register to 0).
    """

    def __init__(self, heap_start, heap_size, pmm=None, pcb=None):
        """
        Args:
            heap_start: virtual address where the heap begins
            heap_size:  initial bytes available for heap allocation
            pmm:        PhysicalMemoryManager (needed for dynamic growth)
            pcb:        owning PCB (needed to update page table on growth)
        """
        self.heap_start = heap_start
        self.heap_size  = heap_size
        self.heap_end   = heap_start + heap_size

        # Back-references for dynamic growth
        self.pmm = pmm
        self.pcb = pcb

        # Separate table of physical pages backing the heap
        # {virtual_page_number: physical_page_number}
        self.heap_pages = {}

        # Start with one giant free block covering the initial heap
        self._blocks = [HeapBlock(heap_start, heap_size, free=True)]

        # Track active allocations: start_vaddr -> size
        # (used to validate free requests)
        self._active_allocs = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def alloc(self, size):
        """
        Allocate *size* bytes from the heap.

        If no contiguous free block is large enough:
          1. Attempt to coalesce adjacent free blocks.
          2. If still insufficient, grow the heap by requesting new pages.
          3. If growth fails (PMM has no free pages), return None.

        Returns:
            int:  virtual start address of the allocated block
            None: if allocation cannot be satisfied
        """
        if size <= 0:
            raise ValueError(f"alloc size must be positive, got {size}")

        # --- First pass: try to find a free block ---
        addr = self._first_fit(size)
        if addr is not None:
            return addr

        # --- Coalesce and retry ---
        self._coalesce()
        addr = self._first_fit(size)
        if addr is not None:
            return addr

        # --- Grow the heap and retry ---
        if self._grow_heap(size):
            self._coalesce()
            addr = self._first_fit(size)
            if addr is not None:
                return addr

        # Allocation failed — heap exhausted
        return None

    def free(self, vaddr):
        """
        Free the block starting at virtual address *vaddr*.

        Raises:
            ValueError: if the address was not an active allocation.
        """
        if vaddr not in self._active_allocs:
            # Check the block list as a fallback (for backward compat)
            for block in self._blocks:
                if block.start == vaddr and not block.free:
                    block.free = True
                    self._active_allocs.pop(vaddr, None)
                    self._coalesce()
                    return
            raise ValueError(f"FreeMemory: address {vaddr} was never allocated")

        # Find the block and mark it free
        for block in self._blocks:
            if block.start == vaddr:
                if block.free:
                    raise ValueError(f"Double-free at virtual addr {vaddr}")
                block.free = True
                del self._active_allocs[vaddr]
                self._coalesce()
                return

        raise ValueError(f"FreeMemory: address {vaddr} not found in block list")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _first_fit(self, size):
        """
        Scan the block list for the first free block >= size.
        If found, carve out exactly `size` bytes and return the start address.
        """
        for block in self._blocks:
            if block.free and block.size >= size:
                # Carve out exactly `size` bytes; remainder stays free
                if block.size > size:
                    remainder = HeapBlock(block.start + size,
                                         block.size  - size,
                                         free=True)
                    idx = self._blocks.index(block)
                    self._blocks.insert(idx + 1, remainder)

                block.size = size
                block.free = False
                self._active_allocs[block.start] = size
                return block.start

        return None

    def _coalesce(self):
        """Merge adjacent free blocks to reduce fragmentation."""
        i = 0
        while i < len(self._blocks) - 1:
            cur = self._blocks[i]
            nxt = self._blocks[i + 1]
            if cur.free and nxt.free:
                cur.size += nxt.size
                self._blocks.pop(i + 1)
            else:
                i += 1

    def _grow_heap(self, min_bytes_needed):
        """
        Expand the heap by requesting new physical pages from the PMM.

        Calculates how many additional pages are needed to provide at
        least min_bytes_needed contiguous bytes at the end of the heap
        (after coalescing with any existing trailing free block).

        Returns True if growth succeeded, False if PMM has no pages.
        """
        if self.pmm is None or self.pcb is None:
            return False   # no PMM reference — cannot grow

        page_size = self.pmm.page_size

        # Determine how much free space exists at the tail of the heap
        trailing_free = 0
        if self._blocks and self._blocks[-1].free:
            trailing_free = self._blocks[-1].size

        bytes_still_needed = min_bytes_needed - trailing_free
        if bytes_still_needed <= 0:
            return True  # already have enough after coalescing

        # Calculate pages needed
        pages_needed = (bytes_still_needed + page_size - 1) // page_size

        # Request pages from PMM
        new_phys_pages = self.pmm.allocate_pages(pages_needed)
        if new_phys_pages is None:
            return False  # out of physical memory

        # Map the new pages into the process's virtual address space
        # They are appended right after the current heap_end
        # Find the virtual page number for the current heap_end
        base_vpage = self.heap_end // page_size

        for i, pp in enumerate(new_phys_pages):
            vpage = base_vpage + i
            self.pcb.page_table[vpage] = pp
            self.pcb.working_set_pages.add(pp)
            self.heap_pages[vpage] = pp

        growth = pages_needed * page_size

        # Extend or create a trailing free block
        if self._blocks and self._blocks[-1].free:
            self._blocks[-1].size += growth
        else:
            new_block = HeapBlock(self.heap_end, growth, free=True)
            self._blocks.append(new_block)

        self.heap_end  += growth
        self.heap_size += growth

        # Update the PCB's heap boundary
        self.pcb.heap_end = self.heap_end

        return True

    def populate_heap_pages(self):
        """
        Populate the heap_pages table from the PCB's page table.
        Called once after initial loading to record which physical
        pages back the initial heap region.
        """
        if self.pcb is None or self.pmm is None:
            return

        page_size = self.pmm.page_size
        for vaddr in range(self.heap_start, self.heap_end, page_size):
            vpage = vaddr // page_size
            if vpage in self.pcb.page_table:
                self.heap_pages[vpage] = self.pcb.page_table[vpage]

    # ------------------------------------------------------------------
    # Debug / Introspection
    # ------------------------------------------------------------------

    def dump(self):
        print(f"  Heap [0x{self.heap_start:04X} - 0x{self.heap_end:04X}] "
              f"({self.heap_size} bytes, {len(self.heap_pages)} pages):")
        for b in self._blocks:
            print(f"    {b}")
        if self._active_allocs:
            print(f"  Active allocations: {len(self._active_allocs)}")
            for addr, sz in self._active_allocs.items():
                print(f"    0x{addr:04X}: {sz} bytes")

    def stats(self):
        """Return a dict with heap usage statistics."""
        total_free = sum(b.size for b in self._blocks if b.free)
        total_used = sum(b.size for b in self._blocks if not b.free)
        largest_free = max((b.size for b in self._blocks if b.free), default=0)
        return {
            'heap_size':      self.heap_size,
            'total_free':     total_free,
            'total_used':     total_used,
            'largest_free':   largest_free,
            'num_blocks':     len(self._blocks),
            'num_allocs':     len(self._active_allocs),
            'num_heap_pages': len(self.heap_pages),
        }
