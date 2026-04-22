# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# PhysicalMemoryManager: owns the raw physical bytearray, tracks free/used
# pages, and provides per-process virtual address translation using the
# process's own page table.
#
# Module 6 enhancements:
#   - Each physical page frame has PageInfo metadata (is_valid, is_dirty, LRU).
#   - A SwapManager handles writing evicted pages to disk and reading them back.
#   - When no free physical frames are available, the LRU page is evicted.
#   - On memory access, if a page is not valid (swapped out), a page fault
#     triggers: evict LRU, swap in the needed page.
#   - Dirty pages are written to swap on eviction; clean pages can be discarded
#     if they already exist in swap (optimization).

import math
from page_info import PageInfo, SwapManager


class PhysicalMemoryManager:
    """
    Manages the single shared physical memory pool for all processes.

    Module 6 additions:
    - self.page_info[]: PageInfo per physical frame (valid, dirty, LRU).
    - self.swap: SwapManager for disk-backed page eviction.
    - self._lru_counter: monotonic counter incremented on every page access.
    - translate() now triggers page-fault handling when a page is invalid.
    """

    NUM_SHARED_REGIONS  = 10
    SHARED_REGION_SIZE  = 1000   # bytes per region (spec)

    def __init__(self, total_size, page_size=256):
        if page_size & (page_size - 1) != 0 or page_size == 0:
            raise ValueError(f"page_size must be a power of 2, got {page_size}")

        self.total_size = total_size
        self.page_size  = page_size
        self.memory     = bytearray(total_size)

        self.offset_bits = int(math.log2(page_size))
        self.offset_mask = page_size - 1

        total_pages  = total_size // page_size
        self.total_pages = total_pages

        # ── Module 6: per-frame metadata ─────────────────────────────
        self.page_info = [PageInfo() for _ in range(total_pages)]
        self.swap      = SwapManager(page_size=page_size)
        self._lru_counter = 0

        # Reverse map: physical_page -> (pid, vpage) for eviction
        self._frame_map = {}  # phys_page -> (pid, vpage)

        # Registry of all PCBs so we can update page tables on eviction
        self._all_pcbs = {}   # pid -> PCB

        # ── Shared memory regions ────────────────────────────────────
        shared_pages_per_region = math.ceil(
            self.SHARED_REGION_SIZE / page_size
        )
        self._shared_pages_per_region = shared_pages_per_region
        shared_total_pages = shared_pages_per_region * self.NUM_SHARED_REGIONS

        # If total memory is too small for shared regions, reduce or skip them
        if shared_total_pages >= total_pages:
            # Not enough memory for shared regions — allocate what we can
            # or skip entirely if very small
            if total_pages > 4:
                # Reserve at most half the pages for shared regions
                shared_total_pages = min(shared_total_pages, total_pages // 2)
                self._first_shared_page = total_pages - shared_total_pages
            else:
                shared_total_pages = 0
                self._first_shared_page = total_pages
        else:
            self._first_shared_page = total_pages - shared_total_pages

        self._shared_region_start_page = {}
        if shared_total_pages > 0:
            for i in range(self.NUM_SHARED_REGIONS):
                region_start = self._first_shared_page + i * shared_pages_per_region
                if region_start + shared_pages_per_region <= total_pages:
                    region_num = i + 1
                    self._shared_region_start_page[region_num] = region_start

        # Free list: all pages except reserved shared ones
        self.free_pages = list(range(self._first_shared_page))

        # Mark shared pages as valid and not evictable
        for pp in range(self._first_shared_page, total_pages):
            self.page_info[pp].is_valid   = True
            self.page_info[pp].owner_pid  = -1   # sentinel: shared
            self.page_info[pp].vpage      = None

        # Track which process owns each page: phys_page -> pid (or None)
        self._page_owner = [None] * total_pages

        # ── Page fault statistics ────────────────────────────────────
        self.total_page_faults  = 0
        self.total_swap_ins     = 0
        self.total_swap_outs    = 0

    # ------------------------------------------------------------------
    # PCB registry (Module 6 — needed for eviction)
    # ------------------------------------------------------------------

    def register_pcb(self, pcb):
        """Register a PCB so we can update its page table on eviction."""
        self._all_pcbs[pcb.pid] = pcb

    def unregister_pcb(self, pcb):
        """Remove a PCB from the registry."""
        self._all_pcbs.pop(pcb.pid, None)

    # ------------------------------------------------------------------
    # LRU tracking
    # ------------------------------------------------------------------

    def _touch_page(self, phys_page):
        """Update the LRU counter for a physical page frame."""
        self._lru_counter += 1
        self.page_info[phys_page].last_access = self._lru_counter

    def _find_lru_page(self, exclude_pids=None):
        """
        Find the least recently used valid page that can be evicted.
        Shared pages and pages owned by excluded PIDs are skipped.
        """
        exclude_pids = exclude_pids or set()
        best_page = None
        best_lru  = float('inf')

        for pp in range(self._first_shared_page):
            info = self.page_info[pp]
            if not info.is_valid:
                continue  # already swapped out / free
            if info.owner_pid is None:
                continue  # free page
            if info.owner_pid in exclude_pids:
                continue
            if info.last_access < best_lru:
                best_lru  = info.last_access
                best_page = pp

        return best_page

    # ------------------------------------------------------------------
    # Page eviction and swap-in
    # ------------------------------------------------------------------

    def _evict_page(self, phys_page):
        """
        Evict a physical page frame to the swap file.

        Steps:
            1. If dirty, write contents to swap.
            2. Mark the frame as invalid in metadata.
            3. Update the owning process's page table (sentinel -1 = swapped).
            4. Return the now-free physical frame number.
        """
        info = self.page_info[phys_page]
        pid  = info.owner_pid
        vp   = info.vpage

        if pid is None or vp is None:
            raise RuntimeError(f"Cannot evict unowned page frame {phys_page}")

        # Write to swap if dirty (or if not yet in swap for safety)
        start = phys_page * self.page_size
        page_data = bytes(self.memory[start:start + self.page_size])

        if info.is_dirty or not self.swap.has_page(pid, vp):
            self.swap.write_page(pid, vp, page_data)
            self.total_swap_outs += 1

        # Clear frame metadata
        info.is_valid   = False
        info.is_dirty   = False
        info.owner_pid  = None
        info.vpage      = None

        # Update the owning process's page table: -1 = "in swap"
        owner_pcb = self._all_pcbs.get(pid)
        if owner_pcb and vp in owner_pcb.page_table:
            owner_pcb.page_table[vp] = -1
            owner_pcb.working_set_pages.discard(phys_page)

        # Remove from frame map
        self._frame_map.pop(phys_page, None)

        print(f"  [VMM] Evicted PID {pid} vpage {vp} from frame {phys_page}"
              f"{' (dirty)' if info.is_dirty else ''}")

        return phys_page

    def _swap_in_page(self, pid, vpage, phys_page):
        """
        Swap a page back into a physical frame from the swap file.
        """
        if self.swap.has_page(pid, vpage):
            data = self.swap.read_page(pid, vpage)
            start = phys_page * self.page_size
            self.memory[start:start + self.page_size] = data
            self.total_swap_ins += 1
            print(f"  [VMM] Swapped in PID {pid} vpage {vpage} to frame {phys_page}")
        else:
            # Page was never written to swap — zero-fill
            start = phys_page * self.page_size
            self.memory[start:start + self.page_size] = bytearray(self.page_size)

        # Update frame metadata
        info = self.page_info[phys_page]
        info.is_valid    = True
        info.is_dirty    = False
        info.owner_pid   = pid
        info.vpage       = vpage
        self._touch_page(phys_page)

        # Update frame map
        self._frame_map[phys_page] = (pid, vpage)

    def handle_page_fault(self, vpage, pcb):
        """
        Handle a page fault for the given virtual page of pcb.

        If the page was swapped out (page_table[vpage] == -1), we need
        a free physical frame. If none available, evict the LRU page.

        Returns:
            physical page number where the page is now loaded.
        """
        self.total_page_faults += 1
        pcb.page_faults += 1

        print(f"  [VMM] Page fault: PID {pcb.pid} vpage {vpage}")

        # Get a free physical frame (may evict)
        phys_page = self._get_free_frame(exclude_pids=set())
        if phys_page is None:
            raise MemoryError(
                f"PID {pcb.pid}: cannot resolve page fault for vpage {vpage} "
                f"— no physical frames available even after eviction"
            )

        # Swap in the page
        self._swap_in_page(pcb.pid, vpage, phys_page)

        # Update the process's page table
        pcb.page_table[vpage] = phys_page
        pcb.working_set_pages.add(phys_page)

        return phys_page

    def _get_free_frame(self, exclude_pids=None):
        """
        Get a free physical frame. If none available, evict LRU.
        """
        if self.free_pages:
            pp = self.free_pages.pop(0)
            return pp

        # No free pages — evict the LRU page
        victim = self._find_lru_page(exclude_pids)
        if victim is not None:
            self._evict_page(victim)
            return victim

        return None

    # ------------------------------------------------------------------
    # Page allocation / deallocation
    # ------------------------------------------------------------------

    def allocate_pages(self, n):
        """
        Allocate n physical pages.
        Module 6: if not enough free pages, evict LRU pages to make room.
        """
        pages = []
        for _ in range(n):
            pp = self._get_free_frame()
            if pp is None:
                # Release any we just got
                self.free_pages.extend(pages)
                return None
            pages.append(pp)
        return pages

    def free_pages_for_process(self, pcb):
        """
        Return all physical pages owned by pcb to the free pool.
        Also clean up swap entries for this process.
        """
        for pp in list(pcb.working_set_pages):
            if pp not in self.free_pages:
                self.free_pages.append(pp)
            info = self.page_info[pp]
            info.is_valid   = True   # frame is free and usable
            info.is_dirty   = False
            info.owner_pid  = None
            info.vpage      = None
            self._page_owner[pp] = None
            self._frame_map.pop(pp, None)

        # Clean up swap entries for this process
        self.swap.remove_all_for_process(pcb.pid)

        pcb.working_set_pages.clear()
        pcb.page_table.clear()

        # Unregister PCB
        self.unregister_pcb(pcb)

    def register_page_mapping(self, phys_page, pid, vpage):
        """
        Register that phys_page now holds (pid, vpage).
        Called by the loader after writing pages.
        """
        info = self.page_info[phys_page]
        info.is_valid   = True
        info.is_dirty   = False
        info.owner_pid  = pid
        info.vpage      = vpage
        self._touch_page(phys_page)
        self._frame_map[phys_page] = (pid, vpage)

    # ------------------------------------------------------------------
    # Shared memory
    # ------------------------------------------------------------------

    def get_shared_region_phys_start(self, region_num):
        if not 1 <= region_num <= self.NUM_SHARED_REGIONS:
            raise ValueError(f"Shared region {region_num} out of range 1-10")
        start_page = self._shared_region_start_page[region_num]
        return start_page * self.page_size

    def map_shared_region_into_process(self, region_num, pcb):
        if not 1 <= region_num <= self.NUM_SHARED_REGIONS:
            raise ValueError(f"Shared region {region_num} out of range 1-10")

        start_phys_page = self._shared_region_start_page[region_num]
        pages_needed    = self._shared_pages_per_region

        next_vpage = max(pcb.page_table.keys(), default=-1) + 1

        for i in range(pages_needed):
            pcb.page_table[next_vpage + i] = start_phys_page + i

        virt_addr = next_vpage * self.page_size
        return virt_addr

    # ------------------------------------------------------------------
    # Read / write via PCB page table
    # ------------------------------------------------------------------

    def translate(self, vaddr, pcb):
        """
        Translate a virtual address using pcb's page table.

        Module 6: if the page is swapped out (page_table[vpage] == -1),
        trigger a page fault to swap it back in.
        """
        if vaddr < 0:
            raise MemoryError(f"PID {pcb.pid}: negative virtual address {vaddr}")

        vpage  = vaddr >> self.offset_bits
        offset = vaddr & self.offset_mask

        if vpage not in pcb.page_table:
            pcb.page_faults += 1
            raise MemoryError(
                f"PID {pcb.pid}: page fault – virtual page {vpage} "
                f"(addr {vaddr}) not mapped"
            )

        ppage = pcb.page_table[vpage]

        # Module 6: check if page is swapped out (sentinel = -1)
        if ppage == -1:
            ppage = self.handle_page_fault(vpage, pcb)

        # Double-check the frame is valid
        if ppage >= 0 and not self.page_info[ppage].is_valid:
            ppage = self.handle_page_fault(vpage, pcb)

        paddr = ppage * self.page_size + offset

        if paddr >= self.total_size:
            raise MemoryError(
                f"PID {pcb.pid}: physical address {paddr} out of bounds"
            )

        # Update LRU on every access
        self._touch_page(ppage)

        return paddr

    def read_byte(self, vaddr, pcb):
        return self.memory[self.translate(vaddr, pcb)]

    def write_byte(self, vaddr, value, pcb):
        paddr = self.translate(vaddr, pcb)
        vpage = vaddr >> self.offset_bits
        ppage = pcb.page_table[vpage]

        # Mark page as dirty on write
        if ppage >= 0:
            self.page_info[ppage].is_dirty = True

        self._bounds_check_write(vaddr, pcb)
        self.memory[paddr] = value & 0xFF

    def read_int(self, vaddr, pcb):
        raw = bytes(self.read_byte(vaddr + i, pcb) for i in range(4))
        return int.from_bytes(raw, byteorder='big', signed=True)

    def write_int(self, vaddr, value, pcb):
        if value < 0:
            b = value.to_bytes(4, byteorder='big', signed=True)
        elif value > 0x7FFFFFFF:
            b = value.to_bytes(4, byteorder='big', signed=False)
        else:
            b = value.to_bytes(4, byteorder='big', signed=True)
        for i, byte in enumerate(b):
            self.write_byte(vaddr + i, byte, pcb)

    # ------------------------------------------------------------------
    # Bounds / write-protection check
    # ------------------------------------------------------------------

    def _bounds_check_write(self, vaddr, pcb):
        vpage = vaddr >> self.offset_bits
        if vpage not in pcb.page_table:
            raise MemoryError(
                f"PID {pcb.pid}: write to unmapped virtual page {vpage}"
            )
        ppage = pcb.page_table[vpage]
        if ppage == -1:
            raise MemoryError(
                f"PID {pcb.pid}: write to swapped-out page {vpage}"
            )
        if (ppage not in pcb.working_set_pages
                and ppage < self._first_shared_page):
            raise MemoryError(
                f"PID {pcb.pid}: write to physical page {ppage} not owned "
                f"by this process (segfault)"
            )

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------

    def dump_free_pages(self):
        print(f"Free pages ({len(self.free_pages)}): {self.free_pages[:20]}"
              f"{'...' if len(self.free_pages) > 20 else ''}")

    def dump_page_info(self):
        """Print status of all physical frames."""
        print(f"\n  Physical Memory: {self.total_pages} frames, "
              f"{len(self.free_pages)} free")
        for pp in range(min(self.total_pages, 40)):
            info = self.page_info[pp]
            if info.owner_pid is not None:
                print(f"    Frame {pp:3d}: {info}")
        swap_stats = self.swap.stats()
        print(f"  Swap: {swap_stats['pages_in_swap']} pages in swap file")

    def print_vm_stats(self):
        """Print Module 6 virtual memory statistics."""
        print(f"  Total page faults : {self.total_page_faults}")
        print(f"  Total swap-ins    : {self.total_swap_ins}")
        print(f"  Total swap-outs   : {self.total_swap_outs}")
        swap_stats = self.swap.stats()
        print(f"  Pages in swap     : {swap_stats['pages_in_swap']}")
