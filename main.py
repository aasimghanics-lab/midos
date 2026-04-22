# Aasim Ghani
# z2051554
# CSCI 480 - Module 6
# main.py: Entry point for MidOS.
# Usage: python main.py <virtual_memory_size> <program1.txt> [program2.txt ...]

import sys
from midos import MidOS


def main():
    if len(sys.argv) < 3:
        print("Usage: python main.py <memory_size> <prog1.txt> [prog2.txt ...]")
        print("Example: python main.py 65536 hello.txt counter.txt")
        return 1

    try:
        mem_size = int(sys.argv[1])
    except ValueError:
        print(f"Error: memory_size must be an integer, got '{sys.argv[1]}'")
        return 1

    program_files = sys.argv[2:]

    try:
        os_instance = MidOS(virtual_memory_size=mem_size, page_size=256)
    except Exception as e:
        print(f"OS init error: {e}")
        return 1

    # Assign descending priorities so first-listed runs first
    base_priority = min(32, len(program_files) + 1)
    for i, prog_file in enumerate(program_files):
        priority = max(1, base_priority - i)
        try:
            os_instance.load_program(prog_file, priority=priority)
        except FileNotFoundError:
            print(f"Error: program file '{prog_file}' not found")
            return 1
        except Exception as e:
            print(f"Error loading '{prog_file}': {e}")
            return 1

    os_instance.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
