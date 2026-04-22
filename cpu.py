# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# CPU: fetch-decode-execute loop aware of the currently running PCB.
# All memory accesses go through the PCB's page table via PhysicalMemoryManager.
# Module 3 opcodes (locks, events, shared mem, alloc, terminate) are handled
# by calling back into the OS kernel via the Scheduler.
# Module 4 adds shared memory mapping, lock/event synchronization, and
# inter-process communication support.

from opcodes import Opcode

INSTRUCTION_SIZE = 12   # 4-byte opcode + 4-byte arg1 + 4-byte arg2


class CPU:
    """
    Virtual CPU.  Each step():
      1. Fetches 12 bytes from memory via current PCB's page table.
      2. Decodes the opcode.
      3. Executes, possibly calling OS callbacks for privileged ops.
      4. Advances the instruction pointer (unless a jump/branch did so).
      5. Decrements ticks_remaining; if zero, signals quantum expiry.
    """

    # Special register indices (mirror Module 1 / Module 2)
    IP              = 11   # Instruction Pointer
    PROC_ID_REG     = 12   # Process ID register
    SP              = 13   # Stack Pointer
    GLOBAL_MEM_REG  = 14   # Global memory start

    def __init__(self, physical_memory, scheduler):
        """
        Args:
            physical_memory: PhysicalMemoryManager
            scheduler:       Scheduler (for OS callbacks)
        """
        self.pmm       = physical_memory
        self.scheduler = scheduler

        # Registers – refreshed on each context switch
        self.registers = [0] * 15

        # Flags
        self.zero_flag = False
        self.sign_flag = False

        # Quantum tracking
        self.ticks_remaining = 10

        # Current PCB (set by OS before each run slice)
        self.current_pcb = None

        # System-wide clock
        self.clock = 0

        # OS-level callbacks injected by the kernel
        # Each is a callable(cpu, pcb, arg) -> None
        self.os_callbacks = {}

        # Set by step() when a context switch is needed
        self.switch_reason = None  # None | 'quantum' | 'sleep' | 'lock' | 'event' | 'exit'

    # ------------------------------------------------------------------
    # Convenience: memory access through current PCB
    # ------------------------------------------------------------------

    def _read_int(self, vaddr):
        return self.pmm.read_int(vaddr, self.current_pcb)

    def _write_int(self, vaddr, value):
        self.pmm.write_int(vaddr, value, self.current_pcb)

    # ------------------------------------------------------------------
    # Fetch / Step
    # ------------------------------------------------------------------

    def fetch(self):
        ip = self.registers[self.IP]
        opcode = self._read_int(ip)
        arg1   = self._read_int(ip + 4)
        arg2   = self._read_int(ip + 8)
        return opcode, arg1, arg2

    def step(self):
        """
        Execute one instruction for the current process.
        Sets self.switch_reason if a context switch should follow.
        Returns False if the process exited, True otherwise.
        """
        self.switch_reason = None
        opcode, arg1, arg2 = self.fetch()

        # Decode & execute
        advance = True   # whether to auto-advance IP by INSTRUCTION_SIZE

        op = opcode  # shorthand

        # ── Arithmetic ──────────────────────────────────────────────
        if op == Opcode.INCR:
            self.registers[arg1] += 1
        elif op == Opcode.ADDI:
            self.registers[arg1] += arg2
        elif op == Opcode.ADDR:
            self.registers[arg1] += self.registers[arg2]

        # ── Data movement ───────────────────────────────────────────
        elif op == Opcode.MOVI:
            self.registers[arg1] = arg2
        elif op == Opcode.MOVR:
            self.registers[arg1] = self.registers[arg2]
        elif op == Opcode.MOVMR:
            addr = self.registers[arg2]
            self.registers[arg1] = self._read_int(addr)
        elif op == Opcode.MOVRM:
            addr = self.registers[arg1]
            self._write_int(addr, self.registers[arg2])
        elif op == Opcode.MOVMM:
            src  = self.registers[arg2]
            dst  = self.registers[arg1]
            self._write_int(dst, self._read_int(src))

        # ── Stack ────────────────────────────────────────────────────
        elif op == Opcode.PUSHR:
            self.registers[self.SP] -= 4
            self._write_int(self.registers[self.SP], self.registers[arg1])
        elif op == Opcode.PUSHI:
            self.registers[self.SP] -= 4
            self._write_int(self.registers[self.SP], arg1)
        elif op == Opcode.POPR:
            self.registers[arg1] = self._read_int(self.registers[self.SP])
            self.registers[self.SP] += 4
        elif op == Opcode.POPM:
            val  = self._read_int(self.registers[self.SP])
            addr = self.registers[arg1]
            self._write_int(addr, val)
            self.registers[self.SP] += 4

        # ── Output ───────────────────────────────────────────────────
        elif op == Opcode.PRINTR:
            print(self.registers[arg1])
        elif op == Opcode.PRINTM:
            print(self._read_int(self.registers[arg1]))
        elif op == Opcode.PRINTCR:
            print(chr(self.registers[arg1] & 0xFF), end='')
        elif op == Opcode.PRINTCM:
            print(chr(self._read_int(self.registers[arg1]) & 0xFF), end='')

        # ── Comparison ───────────────────────────────────────────────
        elif op == Opcode.CMPI:
            result = self.registers[arg1] - arg2
            self.zero_flag = (result == 0)
            self.sign_flag = (result < 0)
        elif op == Opcode.CMPR:
            result = self.registers[arg1] - self.registers[arg2]
            self.zero_flag = (result == 0)
            self.sign_flag = (result < 0)

        # ── Unconditional jumps ──────────────────────────────────────
        elif op == Opcode.JMP:
            self.registers[self.IP] += self.registers[arg1]
            advance = False
        elif op == Opcode.JMPI:
            self.registers[self.IP] += arg1
            advance = False
        elif op == Opcode.JMPA:
            self.registers[self.IP] = arg1
            advance = False

        # ── Conditional jumps – less than ────────────────────────────
        elif op == Opcode.JLT:
            if self.sign_flag:
                self.registers[self.IP] += self.registers[arg1]
                advance = False
        elif op == Opcode.JLTI:
            if self.sign_flag:
                self.registers[self.IP] += arg1
                advance = False
        elif op == Opcode.JLTA:
            if self.sign_flag:
                self.registers[self.IP] = arg1
                advance = False

        # ── Conditional jumps – greater than ────────────────────────
        elif op == Opcode.JGT:
            if not self.sign_flag and not self.zero_flag:
                self.registers[self.IP] += self.registers[arg1]
                advance = False
        elif op == Opcode.JGTI:
            if not self.sign_flag and not self.zero_flag:
                self.registers[self.IP] += arg1
                advance = False
        elif op == Opcode.JGTA:
            if not self.sign_flag and not self.zero_flag:
                self.registers[self.IP] = arg1
                advance = False

        # ── Conditional jumps – equal ────────────────────────────────
        elif op == Opcode.JE:
            if self.zero_flag:
                self.registers[self.IP] += self.registers[arg1]
                advance = False
        elif op == Opcode.JEI:
            if self.zero_flag:
                self.registers[self.IP] += arg1
                advance = False
        elif op == Opcode.JEA:
            if self.zero_flag:
                self.registers[self.IP] = arg1
                advance = False

        # ── Subroutines ──────────────────────────────────────────────
        elif op == Opcode.CALL:
            ret_addr = self.registers[self.IP] + INSTRUCTION_SIZE
            self.registers[self.SP] -= 4
            self._write_int(self.registers[self.SP], ret_addr)
            self.registers[self.IP] += self.registers[arg1]
            advance = False
        elif op == Opcode.CALLM:
            ret_addr = self.registers[self.IP] + INSTRUCTION_SIZE
            self.registers[self.SP] -= 4
            self._write_int(self.registers[self.SP], ret_addr)
            offset = self._read_int(self.registers[arg1])
            self.registers[self.IP] += offset
            advance = False
        elif op == Opcode.RET:
            ret_addr = self._read_int(self.registers[self.SP])
            self.registers[self.SP] += 4
            self.registers[self.IP] = ret_addr
            advance = False

        # ── Program control ──────────────────────────────────────────
        elif op == Opcode.EXIT:
            self.switch_reason = 'exit'
            return False   # signal caller to handle exit

        # ── Sleep ────────────────────────────────────────────────────
        elif op == Opcode.SLEEP:
            cycles = self.registers[arg1] if arg1 else arg2
            self.current_pcb.sleep_counter = max(1, cycles)
            self.switch_reason = 'sleep'
            # IP still advances so we resume at the next instruction
        elif op == Opcode.INPUT:
            try:
                val = int(input())
            except (ValueError, EOFError):
                val = 0
            self.registers[arg1] = val
        elif op == Opcode.INPUTC:
            try:
                ch = input()
                self.registers[arg1] = ord(ch[0]) if ch else 0
            except EOFError:
                self.registers[arg1] = 0

        # ── Priority ────────────────────────────────────────────────
        elif op == Opcode.SETPRIORITY:
            new_prio = max(1, min(32, self.registers[arg1]))
            self.current_pcb.priority          = new_prio
            self.current_pcb.original_priority = new_prio
        elif op == Opcode.SETPRIORITYI:
            new_prio = max(1, min(32, arg1))
            self.current_pcb.priority          = new_prio
            self.current_pcb.original_priority = new_prio

        # ── Locks ────────────────────────────────────────────────────
        elif op == Opcode.ACQUIRELOCK:
            lock_num = self.registers[arg1]
            acquired = self.scheduler.acquire_lock(lock_num, self.current_pcb, self)
            if not acquired:
                self.switch_reason = 'lock'
                advance = False   # IP stays; we'll re-execute after wakeup
                return True       # process is now blocked; switch needed
        elif op == Opcode.ACQUIRELOCKI:
            lock_num = arg1
            acquired = self.scheduler.acquire_lock(lock_num, self.current_pcb, self)
            if not acquired:
                self.switch_reason = 'lock'
                advance = False
                return True
        elif op == Opcode.RELEASELOCK:
            self.scheduler.release_lock(self.registers[arg1], self.current_pcb)
        elif op == Opcode.RELEASELOCKI:
            self.scheduler.release_lock(arg1, self.current_pcb)

        # ── Events ───────────────────────────────────────────────────
        elif op == Opcode.SIGNALEVENT:
            self.scheduler.signal_event(self.registers[arg1])
        elif op == Opcode.SIGNALEVENTII:
            self.scheduler.signal_event(arg1)
        elif op == Opcode.WAITEVENT:
            ev = self.registers[arg1]
            already = self.scheduler.wait_event(ev, self.current_pcb, self)
            if not already:
                self.switch_reason = 'event'
                advance = False
                return True
        elif op == Opcode.WAITEVENTI:
            already = self.scheduler.wait_event(arg1, self.current_pcb, self)
            if not already:
                self.switch_reason = 'event'
                advance = False
                return True

        # ── Shared memory ────────────────────────────────────────────
        elif op == Opcode.MAPSHAREDMEM:
            region = self.registers[arg1]
            vaddr  = self.pmm.map_shared_region_into_process(region, self.current_pcb)
            self.registers[arg2] = vaddr
        elif op == Opcode.MAPSHAREDMEMI:
            vaddr = self.pmm.map_shared_region_into_process(arg1, self.current_pcb)
            if arg2:
                self.registers[arg2] = vaddr

        # ── Dynamic allocation ───────────────────────────────────────
        elif op == Opcode.ALLOC:
            size   = self.registers[arg1]
            dest   = arg2
            vaddr  = self.current_pcb.heap_allocator.alloc(size)
            if vaddr is None:
                self.registers[dest] = 0  # NULL on failure
            else:
                self.registers[dest] = vaddr
        elif op == Opcode.FREEMEMORY:
            vaddr = self.registers[arg1]
            try:
                self.current_pcb.heap_allocator.free(vaddr)
            except ValueError as e:
                print(f"[CPU] FreeMemory error PID {self.current_pcb.pid}: {e}")

        # ── Terminate another process ────────────────────────────────
        elif op == Opcode.TERMINATEPROCESS:
            target_pid = self.registers[arg1]
            self.scheduler.terminate_process(target_pid, self.current_pcb, self)
        elif op == Opcode.TERMINATEPROCESSI:
            self.scheduler.terminate_process(arg1, self.current_pcb, self)

        else:
            raise RuntimeError(
                f"PID {self.current_pcb.pid}: unknown opcode "
                f"0x{opcode:02X} at IP={self.registers[self.IP]}"
            )

        # ── Advance IP ────────────────────────────────────────────────
        if advance:
            self.registers[self.IP] += INSTRUCTION_SIZE

        # ── Clock & quantum ───────────────────────────────────────────
        self.clock += 1
        self.current_pcb.clock_cycles    += 1
        self.current_pcb.ticks_remaining -= 1

        if self.current_pcb.ticks_remaining <= 0 and self.switch_reason is None:
            self.switch_reason = 'quantum'

        return True   # process still running (or switch_reason was set)
