# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# Scheduler: priority-based preemptive scheduler.
# - Always runs the highest-priority READY process.
# - Context switches on: quantum expiry, sleep, lock/event block, exit, I/O.
# - Supports priority inversion mitigation for locks.
# - Maintains separate queues: ready, sleeping, waiting_lock, waiting_event, terminated.

from pcb import ProcessState


class Scheduler:
    """
    Manages all process queues and decides which PCB runs next.

    Queues maintained:
        ready_queue       – READY processes, ordered by priority (desc)
        sleep_queue       – SLEEPING processes; each has a sleep_counter
        lock_wait_queues  – {lock_num: [PCB, ...]} WAITING_LOCK
        event_wait_queues – {event_num: [PCB, ...]} WAITING_EVENT
        terminated_queue  – TERMINATED, pending stats print + cleanup
    """

    NUM_LOCKS  = 10
    NUM_EVENTS = 10

    def __init__(self):
        self.ready_queue       = []   # list[PCB], kept sorted high->low priority
        self.sleep_queue       = []   # list[PCB]
        self.terminated_queue  = []   # list[PCB]

        # {lock_num (1-10): [PCB, ...]}
        self.lock_wait_queues  = {i: [] for i in range(1, self.NUM_LOCKS  + 1)}
        # {event_num (1-10): [PCB, ...]}
        self.event_wait_queues = {i: [] for i in range(1, self.NUM_EVENTS + 1)}

        # Lock ownership: lock_num -> pid or None
        self.lock_owner = {i: None for i in range(1, self.NUM_LOCKS + 1)}

        # Event state: event_num -> bool (True = signaled)
        self.event_signaled = {i: False for i in range(1, self.NUM_EVENTS + 1)}

        self.current_pcb = None   # the PCB currently on the CPU
        self._all_pcbs   = {}     # pid -> PCB (registry for TerminateProcess)

    # ------------------------------------------------------------------
    # Process admission
    # ------------------------------------------------------------------

    def add_process(self, pcb):
        """Add a newly loaded process to the ready queue."""
        self._all_pcbs[pcb.pid] = pcb
        pcb.state = ProcessState.READY
        self._enqueue_ready(pcb)

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def pick_next(self):
        """
        Select and return the next PCB to run.
        Saves state from current_pcb if one is running.
        Returns None if nothing is runnable (should not happen with idle).
        """
        if not self.ready_queue:
            return None
        # Highest priority is first (list is sorted)
        next_pcb = self.ready_queue.pop(0)
        next_pcb.state = ProcessState.RUNNING
        self.current_pcb = next_pcb
        return next_pcb

    def _enqueue_ready(self, pcb):
        """Insert pcb into ready_queue maintaining descending priority order."""
        pcb.state = ProcessState.READY
        # Linear insert (process counts are small)
        for i, p in enumerate(self.ready_queue):
            if pcb.priority > p.priority:
                self.ready_queue.insert(i, pcb)
                return
        self.ready_queue.append(pcb)

    # ------------------------------------------------------------------
    # Context switch helpers
    # ------------------------------------------------------------------

    def context_switch_out(self, pcb, cpu, reason="quantum"):
        """
        Save CPU state into pcb, update stats, move pcb to appropriate queue.

        Args:
            pcb:    the currently running PCB
            cpu:    the CPU instance
            reason: 'quantum' | 'sleep' | 'lock' | 'event' | 'exit' | 'io'
        """
        pcb.save_cpu_state(cpu)

        if reason != 'exit':
            pcb.context_switches += 1

        if reason == 'quantum':
            pcb.ticks_remaining = pcb.time_quantum  # reset quantum
            self._enqueue_ready(pcb)
        elif reason == 'sleep':
            pcb.state = ProcessState.SLEEPING
            self.sleep_queue.append(pcb)
        elif reason == 'lock':
            pcb.state = ProcessState.WAITING_LOCK
            self.lock_wait_queues[pcb.waiting_for_lock].append(pcb)
        elif reason == 'event':
            pcb.state = ProcessState.WAITING_EVENT
            self.event_wait_queues[pcb.waiting_for_event].append(pcb)
        elif reason in ('exit', 'error'):
            pcb.state = ProcessState.TERMINATED
            self.terminated_queue.append(pcb)
        elif reason == 'io':
            self._enqueue_ready(pcb)  # I/O is instant in this sim

        self.current_pcb = None

    def context_switch_in(self, pcb, cpu):
        """Restore pcb's saved state into the CPU."""
        pcb.restore_cpu_state(cpu)
        pcb.state = ProcessState.RUNNING
        self.current_pcb = pcb

    # ------------------------------------------------------------------
    # Clock tick – advance sleeping processes
    # ------------------------------------------------------------------

    def tick_sleeping(self):
        """
        Called every clock tick.  Decrements sleep counters and moves
        processes that have finished sleeping back to the ready queue.
        """
        woke = []
        for pcb in self.sleep_queue:
            pcb.sleep_counter -= 1
            if pcb.sleep_counter <= 0:
                woke.append(pcb)
        for pcb in woke:
            self.sleep_queue.remove(pcb)
            self._enqueue_ready(pcb)

    # ------------------------------------------------------------------
    # Lock operations
    # ------------------------------------------------------------------

    def acquire_lock(self, lock_num, pcb, cpu):
        """
        Try to acquire lock_num for pcb.

        Returns:
            True  – lock acquired, process continues.
            False – lock busy, process is now WAITING_LOCK.
        """
        if not 1 <= lock_num <= self.NUM_LOCKS:
            raise ValueError(f"Lock number {lock_num} out of range 1-10")

        owner_pid = self.lock_owner[lock_num]

        # Re-entrant: process already owns this lock – no-op
        if owner_pid == pcb.pid:
            return True

        if owner_pid is None:
            # Lock is free
            self.lock_owner[lock_num] = pcb.pid
            pcb.locks_held.add(lock_num)
            return True
        else:
            # Lock is held by someone else – block
            pcb.waiting_for_lock = lock_num

            # ── Priority inversion mitigation ───────────────────────
            # If we (blocker) have higher priority than the lock holder,
            # temporarily boost the holder's priority.
            holder = self._all_pcbs.get(owner_pid)
            if holder and holder.priority < pcb.priority:
                holder.priority = pcb.priority
                # Re-sort the ready queue if holder is in it
                if holder in self.ready_queue:
                    self.ready_queue.remove(holder)
                    self._enqueue_ready(holder)

            self.context_switch_out(pcb, cpu, reason='lock')
            return False

    def release_lock(self, lock_num, pcb):
        """
        Release lock_num held by pcb.
        Unblocks the highest-priority waiter (if any).
        Restores priority inversion boost if applicable.
        """
        if not 1 <= lock_num <= self.NUM_LOCKS:
            raise ValueError(f"Lock number {lock_num} out of range 1-10")

        if self.lock_owner[lock_num] != pcb.pid:
            # Not owner – silently ignore (defensive)
            return

        self.lock_owner[lock_num] = None
        pcb.locks_held.discard(lock_num)

        # Restore priority if it was boosted for inversion
        if pcb.priority != pcb.original_priority and not pcb.locks_held:
            pcb.priority = pcb.original_priority

        # Wake the highest-priority waiter
        waiters = self.lock_wait_queues[lock_num]
        if waiters:
            # Sort by priority descending, wake only the best
            waiters.sort(key=lambda p: p.priority, reverse=True)
            waiter = waiters.pop(0)
            waiter.waiting_for_lock = None
            self.lock_owner[lock_num] = waiter.pid
            waiter.locks_held.add(lock_num)
            self._enqueue_ready(waiter)

    def force_release_all_locks(self, pcb):
        """Release every lock held by pcb (called on process exit/terminate)."""
        for lock_num in list(pcb.locks_held):
            self.lock_owner[lock_num] = None
            pcb.locks_held.discard(lock_num)
            waiters = self.lock_wait_queues[lock_num]
            if waiters:
                waiters.sort(key=lambda p: p.priority, reverse=True)
                waiter = waiters.pop(0)
                waiter.waiting_for_lock = None
                self.lock_owner[lock_num] = waiter.pid
                waiter.locks_held.add(lock_num)
                self._enqueue_ready(waiter)

    # ------------------------------------------------------------------
    # Event operations
    # ------------------------------------------------------------------

    def signal_event(self, event_num):
        """
        Signal event_num.
        Wakes the highest-priority process waiting on it (if any).
        The event becomes non-signaled once a waiter is woken.
        If no one is waiting, the event stays signaled.
        """
        if not 1 <= event_num <= self.NUM_EVENTS:
            raise ValueError(f"Event number {event_num} out of range 1-10")

        waiters = self.event_wait_queues[event_num]
        if waiters:
            waiters.sort(key=lambda p: p.priority, reverse=True)
            waiter = waiters.pop(0)
            waiter.waiting_for_event = None
            self._enqueue_ready(waiter)
            self.event_signaled[event_num] = False
        else:
            self.event_signaled[event_num] = True

    def wait_event(self, event_num, pcb, cpu):
        """
        Block pcb on event_num.
        If event is already signaled, consume the signal and continue.

        Returns:
            True  – event was already signaled, process continues.
            False – process now blocked WAITING_EVENT.
        """
        if not 1 <= event_num <= self.NUM_EVENTS:
            raise ValueError(f"Event number {event_num} out of range 1-10")

        if self.event_signaled[event_num]:
            self.event_signaled[event_num] = False
            return True  # already signaled – consume and proceed

        pcb.waiting_for_event = event_num
        self.context_switch_out(pcb, cpu, reason='event')
        return False

    # ------------------------------------------------------------------
    # Process termination
    # ------------------------------------------------------------------

    def terminate_process(self, pid, killer_pcb, cpu):
        """
        Forcibly terminate the process with the given pid.
        Searches all queues, removes it, and marks as TERMINATED.
        """
        target = self._all_pcbs.get(pid)
        if target is None:
            print(f"[Scheduler] TerminateProcess: PID {pid} not found")
            return
        if target.state == ProcessState.TERMINATED:
            return

        # Remove from wherever it lives
        for queue in ([self.ready_queue, self.sleep_queue] +
                      list(self.lock_wait_queues.values()) +
                      list(self.event_wait_queues.values())):
            if target in queue:
                queue.remove(target)
                break

        target.state = ProcessState.TERMINATED
        self.terminated_queue.append(target)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def all_done(self):
        """True if no READY, RUNNING, SLEEPING, or WAITING processes remain."""
        has_live = bool(self.ready_queue or self.sleep_queue or self.current_pcb)
        if has_live:
            return False
        for q in list(self.lock_wait_queues.values()) + list(self.event_wait_queues.values()):
            if q:
                return False
        return True

    def dump_queues(self):
        print(f"[Scheduler] Ready: {[p.pid for p in self.ready_queue]}")
        print(f"[Scheduler] Sleeping: {[p.pid for p in self.sleep_queue]}")
        print(f"[Scheduler] Running: "
              f"{self.current_pcb.pid if self.current_pcb else None}")
