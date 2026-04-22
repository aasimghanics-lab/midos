# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# Process Control Block (PCB): stores all state for a process including registers,
# flags, memory layout, scheduling info, and statistics.


from enum import Enum, auto


class ProcessState(Enum):
    """All possible states a process can be in."""
    NEW        = auto()  # Just created, not yet ready
    READY      = auto()  # Ready to run, waiting for CPU
    RUNNING    = auto()  # Currently executing on CPU
    SLEEPING   = auto()  # Waiting after a Sleep instruction
    WAITING_LOCK  = auto()  # Blocked trying to acquire a lock
    WAITING_EVENT = auto()  # Blocked waiting on an event signal
    TERMINATED = auto()  # Finished, pending cleanup


class PCB:
    """
    Process Control Block.
    Stores the complete state of a process so it can be context-switched
    in and out of the CPU.
    """

    # Class-level counter so each process gets a unique ID
    _next_pid = 1

    def __init__(self, filename, priority=1, time_quantum=10):
        """
        Create a new PCB for a process loaded from filename.

        Args:
            filename:     Path to the .asm program file
            priority:     Scheduling priority (1 = lowest non-idle, 32 = highest)
            time_quantum: Max clock cycles before forced context switch
        """
        # --- Identity ---
        self.pid      = PCB._next_pid
        PCB._next_pid += 1
        self.filename = filename

        # --- Memory layout (set by the OS loader) ---
        self.code_size        = 0
        self.stack_size       = 4       # starts at 4 bytes as per spec
        self.data_size        = 512     # global data region
        self.heap_size        = 512     # heap region
        self.process_mem_size = 0       # total virtual memory for this process

        # Virtual addresses (set by loader)
        self.code_start        = 0
        self.global_data_start = 0
        self.heap_start        = 0
        self.heap_end          = 0
        self.stack_start       = 0
        self.stack_top         = 0      # current SP (grows down)

        # Page table owned by this process: {virtual_page -> physical_page}
        self.page_table   = {}
        # Set of physical page numbers allocated to this process
        self.working_set_pages = set()

        # --- Saved CPU state (filled on context switch out) ---
        # Registers 0-14; index 0 unused; 11=IP, 12=PID reg, 13=SP, 14=global_mem_start
        self.registers  = [0] * 15
        self.zero_flag  = False
        self.sign_flag  = False

        # --- Scheduling ---
        self.state           = ProcessState.NEW
        self.priority        = priority
        self.original_priority = priority   # for priority-inversion restoration
        self.time_quantum    = time_quantum
        self.ticks_remaining = time_quantum  # counts down while RUNNING

        # --- Synchronization ---
        self.waiting_for_lock  = None   # lock number (1-10) we are blocked on
        self.waiting_for_event = None   # event number (1-10) we are blocked on
        self.locks_held        = set()  # set of lock numbers currently owned

        # --- Statistics ---
        self.clock_cycles     = 0   # total ticks consumed
        self.context_switches = 0   # how many times swapped out
        self.page_faults      = 0   # page faults incurred

        # --- Sleep ---
        self.sleep_counter = 0  # ticks remaining in sleep

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def save_cpu_state(self, cpu):
        """
        Copy the live CPU state into this PCB.
        Called just before this process is switched OUT.
        """
        self.registers = cpu.registers[:]
        self.zero_flag = cpu.zero_flag
        self.sign_flag = cpu.sign_flag
        self.ticks_remaining = cpu.ticks_remaining

    def restore_cpu_state(self, cpu):
        """
        Copy this PCB's saved state back into the CPU.
        Called just before this process is switched IN.
        """
        cpu.registers    = self.registers[:]
        cpu.zero_flag    = self.zero_flag
        cpu.sign_flag    = self.sign_flag
        cpu.ticks_remaining = self.ticks_remaining

    def print_stats(self):
        """Print end-of-life statistics for this process."""
        print(f"\n{'='*50}")
        print(f"  Process {self.pid} ({self.filename}) TERMINATED")
        print(f"{'='*50}")
        print(f"  Page faults      : {self.page_faults}")
        print(f"  Context switches : {self.context_switches}")
        print(f"  Clock cycles     : {self.clock_cycles}")
        print(f"{'='*50}\n")

    def __repr__(self):
        return (f"PCB(pid={self.pid}, file='{self.filename}', "
                f"state={self.state.name}, priority={self.priority})")
