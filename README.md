# MidOS — Virtual Operating System

**CSCI 480 – Principles of Operating Systems**
Aasim Ghani | z2051554

---

## Overview

MidOS is a simulated operating system built in Python that runs on a custom virtual machine. It supports multiple processes, priority-based scheduling, virtual memory with paging and swap, dynamic heap allocation, locks, events, and shared memory.

## How to Run

```
python main.py <memory_size> <program1.txt> [program2.txt ...]
```

**Example:**
```
python main.py 65536 hello.txt counter.txt
```

- `memory_size` — total virtual memory in bytes (e.g. 65536)
- Each program file is an assembly `.txt` file loaded as a separate process

## How to Run Tests

```
python test_module6.py
```

All 20 tests pass.

## Project Structure

| File | Description |
|------|-------------|
| `main.py` | Entry point |
| `midos.py` | OS kernel — wires everything together |
| `cpu.py` | Virtual CPU — fetch/decode/execute loop |
| `physical_memory.py` | Physical memory manager — paging, translation, swap |
| `os_loader.py` | Loads assembly programs into memory, builds PCBs |
| `scheduler.py` | Priority-based preemptive scheduler |
| `pcb.py` | Process Control Block |
| `heap_allocator.py` | Per-process first-fit heap allocator with dynamic growth |
| `page_info.py` | Page frame metadata (valid/dirty flags) and swap file manager |
| `opcodes.py` | Full instruction set enumeration |

## Modules Completed

| Module | Feature |
|--------|---------|
| 1 | Virtual CPU, instruction set, basic memory |
| 2 | Paging and virtual address translation |
| 3 | Process management, scheduling, context switching, locks, events |
| 4 | Shared memory (10 pre-allocated regions) |
| 5 | Dynamic heap allocation with coalescing and growth |
| 6 | Virtual memory — LRU page eviction and disk swap |

## Assembly Language

Programs are written in a simple assembly language. Example:

```
movi r1, #42      ; load 42 into r1
printr r1         ; print it
exit              ; terminate
```

Each instruction is 12 bytes (4-byte opcode + two 4-byte arguments). The CPU has 14 registers — r1 through r10 are general purpose, r11 is the instruction pointer, r13 is the stack pointer, and r14 holds the global data start address.

