# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# MidOS: the operating system kernel.
# Wires together PhysicalMemoryManager, OSLoader, Scheduler, and CPU.
# Implements the main run-loop, context switching, and process lifecycle.

from physical_memory import PhysicalMemoryManager
from os_loader      import OSLoader
from scheduler      import Scheduler
from cpu            import CPU
from heap_allocator import HeapAllocator
from pcb            import ProcessState


# Per the spec: time quantum = 10, idle quantum = 5
DEFAULT_QUANTUM  = 10
IDLE_QUANTUM     = 5
IDLE_PRIORITY    = 0   # always lowest

# Idle process filename (spec: idle.txt)
IDLE_PROGRAM = "idle.txt"


class MidOS:
    """
    MidOS kernel.

    Usage:
        os = MidOS(virtual_memory_size=65536, page_size=256)
        os.load_program('prog1.txt', priority=10)
        os.load_program('prog2.txt', priority=5)
        os.run()
    """

    def __init__(self, virtual_memory_size=65536, page_size=256):
        """
        Args:
            virtual_memory_size: total physical bytes available for all processes
            page_size:           page size in bytes (power of 2, default 256)
        """
        self.pmm       = PhysicalMemoryManager(virtual_memory_size, page_size)
        self.loader    = OSLoader(self.pmm)
        self.scheduler = Scheduler()
        self.cpu       = CPU(self.pmm, self.scheduler)

        self._idle_pcb = None

        # Load the idle process (must exist as idle.txt)
        self._load_idle()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_program(self, filename, priority=1):
        """
        Load a user program and add it to the ready queue.

        Args:
            filename: path to .asm program file
            priority: 1 (lowest user) – 32 (highest user)
        """
        priority = max(1, min(32, priority))  # clamp
        pcb = self.loader.load(filename, priority=priority,
                               time_quantum=DEFAULT_QUANTUM)
        # Attach a heap allocator to the PCB with PMM reference for dynamic growth
        pcb.heap_allocator = HeapAllocator(pcb.heap_start,
                                           pcb.heap_end - pcb.heap_start,
                                           pmm=self.pmm, pcb=pcb)
        pcb.heap_allocator.populate_heap_pages()
        self.scheduler.add_process(pcb)
        return pcb

    def run(self):
        """
        Main OS run-loop.
        Keeps executing until all non-idle processes have terminated.
        """
        print("\n" + "="*60)
        print("  MidOS starting up")
        print("="*60 + "\n")

        while True:
            # ── Pick next process ────────────────────────────────────
            pcb = self.scheduler.pick_next()
            if pcb is None:
                # Nothing at all – we're done (idle exits only when all done)
                break

            # ── Context switch IN ────────────────────────────────────
            self.scheduler.context_switch_in(pcb, self.cpu)
            self.cpu.current_pcb = pcb

            print(f"[OS] Scheduling PID {pcb.pid} ('{pcb.filename}', "
                  f"priority={pcb.priority}, "
                  f"quantum={pcb.ticks_remaining})")

            # ── Run the process for its quantum ─────────────────────
            keep_running = True
            while keep_running and self.cpu.switch_reason is None:
                try:
                    keep_running = self.cpu.step()
                except MemoryError as e:
                    self._handle_memory_error(pcb, e)
                    self.cpu.switch_reason = 'error'
                    keep_running = False
                except RuntimeError as e:
                    self._handle_runtime_error(pcb, e)
                    self.cpu.switch_reason = 'error'
                    keep_running = False

            # ── Context switch OUT ───────────────────────────────────
            reason = self.cpu.switch_reason or 'exit'
            self._handle_switch_out(pcb, reason)

            # ── Tick sleeping processes ──────────────────────────────
            self.scheduler.tick_sleeping()

            # ── Check if we should stop (all real procs done) ────────
            if self._only_idle_remains():
                print("[OS] All user processes complete. Shutting down.")
                break

        self._print_final_stats()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_idle(self):
        """Load the idle process. Creates a minimal idle.txt if missing."""
        import os
        if not os.path.exists(IDLE_PROGRAM):
            self._write_default_idle()
        try:
            pcb = self.loader.load(IDLE_PROGRAM, priority=IDLE_PRIORITY,
                                   time_quantum=IDLE_QUANTUM)
            pcb.heap_allocator = HeapAllocator(
                pcb.heap_start, pcb.heap_end - pcb.heap_start,
                pmm=self.pmm, pcb=pcb
            )
            pcb.heap_allocator.populate_heap_pages()
            self._idle_pcb = pcb
            self.scheduler.add_process(pcb)
        except Exception as e:
            print(f"[OS] Warning: could not load idle process: {e}")
            self._idle_pcb = None

    @staticmethod
    def _write_default_idle():
        """Write the default idle.txt (tight loop printing 20)."""
        with open(IDLE_PROGRAM, 'w') as f:
            f.write("; idle process - tight loop printing 20\n")
            f.write("movi r1, #20\n")
            f.write("; loop:\n")
            f.write("printr r1\n")
            f.write("jmpi #-12\n")   # jump back 1 instruction (12 bytes)

    def _handle_switch_out(self, pcb, reason):
        """Handle whatever reason caused the context switch."""
        if reason == 'exit':
            print(f"[OS] PID {pcb.pid} exited normally.")
            self.scheduler.force_release_all_locks(pcb)
            self.scheduler.context_switch_out(pcb, self.cpu, reason='exit')
            pcb.print_stats()
            self.pmm.free_pages_for_process(pcb)

        elif reason == 'error':
            print(f"[OS] PID {pcb.pid} terminated due to error.")
            self.scheduler.force_release_all_locks(pcb)
            self.scheduler.context_switch_out(pcb, self.cpu, reason='error')
            pcb.state = ProcessState.TERMINATED
            pcb.print_stats()
            self.pmm.free_pages_for_process(pcb)

        elif reason == 'quantum':
            print(f"[OS] PID {pcb.pid} quantum expired. Context switching.")
            self.scheduler.context_switch_out(pcb, self.cpu, reason='quantum')

        elif reason == 'sleep':
            print(f"[OS] PID {pcb.pid} sleeping for {pcb.sleep_counter} ticks.")
            self.scheduler.context_switch_out(pcb, self.cpu, reason='sleep')

        elif reason == 'lock':
            lock_num = pcb.waiting_for_lock
            print(f"[OS] PID {pcb.pid} blocked waiting for lock {lock_num}.")
            # context_switch_out already called inside acquire_lock

        elif reason == 'event':
            ev_num = pcb.waiting_for_event
            print(f"[OS] PID {pcb.pid} blocked waiting for event {ev_num}.")
            # context_switch_out already called inside wait_event

        else:
            # Shouldn't happen – treat as quantum
            self.scheduler.context_switch_out(pcb, self.cpu, reason='quantum')

        # Reset CPU state for next process
        self.cpu.switch_reason   = None
        self.cpu.current_pcb     = None

    def _handle_memory_error(self, pcb, exc):
        """Print register dump and terminate the process on memory fault."""
        print(f"\n[OS] MEMORY ERROR in PID {pcb.pid}: {exc}")
        self._dump_registers(pcb)

    def _handle_runtime_error(self, pcb, exc):
        """Print register dump and terminate the process on illegal opcode etc."""
        print(f"\n[OS] RUNTIME ERROR in PID {pcb.pid}: {exc}")
        self._dump_registers(pcb)

    def _dump_registers(self, pcb):
        """Print register state at time of error."""
        regs = self.cpu.registers
        print(f"  IP={regs[self.cpu.IP]}  SP={regs[self.cpu.SP]}")
        for i in range(1, 11):
            print(f"  r{i:<2} = {regs[i]}")

    def _only_idle_remains(self):
        """True if the only process left alive is the idle process."""
        if self._idle_pcb is None:
            return self.scheduler.all_done()

        idle_pid = self._idle_pcb.pid
        sched    = self.scheduler

        # Check ready queue
        real_ready = [p for p in sched.ready_queue if p.pid != idle_pid]
        if real_ready:
            return False

        # Check sleeping
        real_sleeping = [p for p in sched.sleep_queue if p.pid != idle_pid]
        if real_sleeping:
            return False

        # Check lock/event waiters
        for q in (list(sched.lock_wait_queues.values()) +
                  list(sched.event_wait_queues.values())):
            real_waiters = [p for p in q if p.pid != idle_pid]
            if real_waiters:
                return False

        # Check if current running process is not idle
        if sched.current_pcb and sched.current_pcb.pid != idle_pid:
            return False

        return True

    def _print_final_stats(self):
        print("\n" + "="*60)
        print("  MidOS Final Statistics")
        print("="*60)
        print(f"  Total clock cycles: {self.cpu.clock}")
        for pid, pcb in self.scheduler._all_pcbs.items():
            if pid == (self._idle_pcb.pid if self._idle_pcb else -1):
                continue
            print(f"  PID {pid} ({pcb.filename}): "
                  f"{pcb.clock_cycles} cycles, "
                  f"{pcb.context_switches} switches, "
                  f"{pcb.page_faults} page faults")
        # Module 6: Virtual memory statistics
        print("-"*60)
        print("  Virtual Memory Statistics:")
        self.pmm.print_vm_stats()
        print("="*60 + "\n")

        # Module 6: Clean up the swap file
        self.pmm.swap.cleanup()
