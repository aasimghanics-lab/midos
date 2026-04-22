# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# OS Loader: allocates per-process virtual memory, sets up page tables,
# and loads assembly programs into the correct physical pages.

from opcodes import Opcode
from pcb import PCB, ProcessState


class OSLoader:
    """
    Loads a program file into a new process, building its PCB and
    mapping its virtual pages to available physical pages.

    Virtual address space layout per process (all sizes configurable):
        [0 ........... code_end)          code
        [code_end .... global_end)        global data  (512 bytes, zeroed)
        [global_end .. heap_end)          heap         (512 bytes)
        [heap_end .... stack_top)         stack        (grows downward)
    """

    # Per-process memory region sizes (bytes) - match spec defaults
    GLOBAL_DATA_SIZE = 512
    HEAP_SIZE        = 512
    INITIAL_STACK    = 4        # starts at 4 bytes, grows down

    def __init__(self, physical_memory_manager):
        """
        Args:
            physical_memory_manager: The OS-level PhysicalMemoryManager that
                                     owns the raw bytearray and free-page list.
        """
        self.pmm = physical_memory_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, filename, priority=1, time_quantum=10):
        """
        Parse *filename*, allocate physical pages, build a PCB, and
        return the ready-to-schedule PCB.

        Args:
            filename:     path to .asm program
            priority:     scheduling priority (1–32)
            time_quantum: clock cycles before forced context switch

        Returns:
            PCB: fully initialised, state=READY

        Raises:
            RuntimeError: if not enough free pages to load the program
        """
        instructions = self._parse(filename)

        # --- Calculate sizes ---
        code_bytes = len(instructions) * 12         # 12 bytes / instruction
        page_size  = self.pmm.page_size

        code_pages   = self._pages_needed(code_bytes,                 page_size)
        global_pages = self._pages_needed(self.GLOBAL_DATA_SIZE,      page_size)
        heap_pages   = self._pages_needed(self.HEAP_SIZE,             page_size)
        stack_pages  = self._pages_needed(self.INITIAL_STACK,         page_size)
        total_pages  = code_pages + global_pages + heap_pages + stack_pages

        # --- Allocate physical pages ---
        phys_pages = self.pmm.allocate_pages(total_pages)
        if phys_pages is None:
            raise RuntimeError(
                f"Not enough free memory to load '{filename}' "
                f"(need {total_pages} pages, have {len(self.pmm.free_pages)})"
            )

        # --- Build PCB ---
        pcb = PCB(filename, priority=priority, time_quantum=time_quantum)

        # Virtual layout starts at 0 for every process
        virt_code_start   = 0
        virt_global_start = code_pages   * page_size
        virt_heap_start   = virt_global_start + global_pages * page_size
        virt_stack_base   = virt_heap_start   + heap_pages   * page_size
        virt_stack_top    = virt_stack_base   + stack_pages  * page_size

        pcb.code_start        = virt_code_start
        pcb.code_size         = code_bytes
        pcb.global_data_start = virt_global_start
        pcb.heap_start        = virt_heap_start
        pcb.heap_end          = virt_heap_start + self.HEAP_SIZE
        pcb.stack_start       = virt_stack_base
        pcb.stack_top         = virt_stack_top   # SP starts at top
        pcb.process_mem_size  = virt_stack_top
        pcb.working_set_pages = set(phys_pages)

        # Build page table: virtual page i -> physical page phys_pages[i]
        for vp, pp in enumerate(phys_pages):
            pcb.page_table[vp] = pp

        # --- Initialise register snapshot in PCB ---
        # Per the CPU convention (from Module 1):
        #   reg[11] = IP  (Instruction Pointer) - starts at 0
        #   reg[12] = CURRENT_PROCESS_ID        - holds the process id
        #   reg[13] = SP  (Stack Pointer)       - top of stack
        #   reg[14] = GLOBAL_MEM_START          - virtual address of global data
        pcb.registers[11] = 0                  # IP starts at virtual address 0
        pcb.registers[12] = pcb.pid            # process ID
        pcb.registers[13] = virt_stack_top     # SP = top of stack
        pcb.registers[14] = virt_global_start  # global memory start

        # --- Register PCB with PMM (Module 6: needed for page eviction) ---
        self.pmm.register_pcb(pcb)

        # --- Register page mappings (Module 6: track frame ownership) ---
        for vp, pp in pcb.page_table.items():
            self.pmm.register_page_mapping(pp, pcb.pid, vp)

        # --- Write instructions into physical memory via page table ---
        self._write_instructions(instructions, pcb)

        # --- Zero out global data region ---
        self._zero_region(virt_global_start,
                          virt_global_start + self.GLOBAL_DATA_SIZE, pcb)

        pcb.state = ProcessState.READY
        print(f"[Loader] Loaded '{filename}' as PID {pcb.pid} "
              f"({code_bytes} bytes, {total_pages} pages, priority={priority})")
        return pcb

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pages_needed(size, page_size):
        return max(1, (size + page_size - 1) // page_size)

    def _write_instructions(self, instructions, pcb):
        """Write parsed instructions into memory via the PCB's page table."""
        addr = 0
        for opcode, a1, a2 in instructions:
            self._write_int(addr,     opcode.value, pcb)
            self._write_int(addr + 4, a1,           pcb)
            self._write_int(addr + 8, a2,           pcb)
            addr += 12

    def _zero_region(self, start, end, pcb):
        """Zero a virtual address range using the PCB's page table."""
        for addr in range(start, end):
            self._write_byte(addr, 0, pcb)

    def _virt_to_phys(self, vaddr, pcb):
        """Translate virtual address using PCB page table."""
        page_size = self.pmm.page_size
        vpage  = vaddr >> self.pmm.offset_bits
        offset = vaddr & self.pmm.offset_mask
        if vpage not in pcb.page_table:
            raise MemoryError(
                f"PID {pcb.pid}: page fault at virtual address {vaddr} "
                f"(page {vpage} not mapped) during load"
            )
        ppage = pcb.page_table[vpage]
        return ppage * page_size + offset

    def _write_byte(self, vaddr, value, pcb):
        paddr = self._virt_to_phys(vaddr, pcb)
        self.pmm.memory[paddr] = value & 0xFF

    def _write_int(self, vaddr, value, pcb):
        """Write a 4-byte big-endian integer at virtual address."""
        if value < 0:
            b = value.to_bytes(4, byteorder='big', signed=True)
        elif value > 0x7FFFFFFF:
            b = value.to_bytes(4, byteorder='big', signed=False)
        else:
            b = value.to_bytes(4, byteorder='big', signed=True)
        for i, byte in enumerate(b):
            self._write_byte(vaddr + i, byte, pcb)

    # ------------------------------------------------------------------
    # Parser (reused from Module 2 program_loader)
    # ------------------------------------------------------------------

    def _parse(self, filename):
        """Parse assembly file; return list of (Opcode, arg1, arg2)."""
        instructions = []
        with open(filename, 'r') as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.split(';')[0].strip()
                if not line:
                    continue
                try:
                    instructions.append(self._parse_line(line))
                except Exception as e:
                    raise SyntaxError(f"{filename}:{lineno}: {e}")
        return instructions

    def _parse_line(self, line):
        parts = line.replace(',', '').split()
        mnemonic = parts[0].upper()
        arg1 = parts[1] if len(parts) > 1 else None
        arg2 = parts[2] if len(parts) > 2 else None
        mnemonic = self._resolve_variant(mnemonic, arg1, arg2)
        try:
            opcode = Opcode[mnemonic]
        except KeyError:
            raise ValueError(f"Unknown opcode: {mnemonic}")
        a1 = self._parse_operand(arg1) if arg1 else 0
        a2 = self._parse_operand(arg2) if arg2 else 0
        return opcode, a1, a2

    @staticmethod
    def _resolve_variant(mnemonic, arg1, arg2):
        if mnemonic.endswith('I') or mnemonic.endswith('A'):
            return mnemonic
        imm_variants = {"JMP", "JLT", "JGT", "JE",
                        "ACQUIRELOCK", "RELEASELOCK",
                        "SIGNALEVENT", "WAITEVENT",
                        "MAPSHAREDMEM", "TERMINATEPROCESS",
                        "SETPRIORITY"}
        def is_imm(op):
            return op and (op.startswith('#') or op.startswith('$') or op.startswith('@'))
        if mnemonic in imm_variants and is_imm(arg1):
            return mnemonic + 'I'
        return mnemonic

    @staticmethod
    def _parse_operand(operand):
        if operand.startswith('r'):
            reg = int(operand[1:])
            if not 1 <= reg <= 14:
                raise ValueError(f"Register out of range: {operand}")
            return reg
        elif operand.startswith('#') or operand.startswith('$'):
            return int(operand[1:])
        elif operand.startswith('@'):
            if len(operand) != 2:
                raise ValueError(f"Bad char constant: {operand}")
            return ord(operand[1])
        else:
            return int(operand)
