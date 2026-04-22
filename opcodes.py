# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# Opcode enumeration extended with OS-level instructions required for Module 3.

from enum import IntEnum

class Opcode(IntEnum):
    """
    Full opcode table for the MidOS virtual machine.
    Module 3 adds process management, locking, events, shared memory,
    dynamic allocation, and termination opcodes.
    """

    # Arithmetic
    INCR   = 0x01
    ADDI   = 0x02
    ADDR   = 0x03

    # Data movement
    MOVI   = 0x04
    MOVR   = 0x05
    MOVMR  = 0x06
    MOVRM  = 0x07
    MOVMM  = 0x08

    # Stack
    PUSHR  = 0x09
    PUSHI  = 0x0A
    POPR   = 0x0C
    POPM   = 0x0D

    # Output
    PRINTR  = 0x0B
    PRINTM  = 0x0E
    PRINTCR = 0x0F
    PRINTCM = 0x10

    # Unconditional jumps
    JMP    = 0x11
    JMPI   = 0x12
    JMPA   = 0x13

    # Comparison
    CMPI   = 0x14
    CMPR   = 0x15

    # Conditional jumps - less than
    JLT    = 0x16
    JLTI   = 0x17
    JLTA   = 0x18

    # Conditional jumps - greater than
    JGT    = 0x19
    JGTI   = 0x1A
    JGTA   = 0x1B

    # Conditional jumps - equal
    JE     = 0x1C
    JEI    = 0x1D
    JEA    = 0x1E

    # Subroutines
    CALL   = 0x1F
    CALLM  = 0x20
    RET    = 0x21

    # Program control
    EXIT   = 0x22

    # System / I-O
    SLEEP         = 0x23
    INPUT         = 0x24
    INPUTC        = 0x25
    SETPRIORITY   = 0x26
    SETPRIORITYI  = 0x27

    # Module 3: Locks
    ACQUIRELOCK   = 0x28
    ACQUIRELOCKI  = 0x29
    RELEASELOCK   = 0x2A
    RELEASELOCKI  = 0x2B

    # Module 3: Events
    SIGNALEVENT   = 0x2C
    SIGNALEVENTII = 0x2D
    WAITEVENT     = 0x2E
    WAITEVENTI    = 0x2F

    # Module 3: Shared memory
    MAPSHAREDMEM  = 0x30
    MAPSHAREDMEMI = 0x31

    # Module 3: Dynamic allocation
    ALLOC         = 0x32
    FREEMEMORY    = 0x33

    # Module 3: Process management
    TERMINATEPROCESS  = 0x34
    TERMINATEPROCESSI = 0x35
