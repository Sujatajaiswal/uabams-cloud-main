import struct
import sys
import os
import argparse
from collections import deque

def read_adxl(filepath, tail_n, head_n):
    lines = deque(maxlen=tail_n) if tail_n else []
    count_read = 0
    with open(filepath, "rb") as f:
        while True:
            header = f.read(9)
            if len(header) < 9: break
            start_count, sample_count = struct.unpack('<QB', header)
            
            for _ in range(sample_count):
                sample_data = f.read(20)
                if len(sample_data) < 20: break
                count, x, y, z = struct.unpack('<Qfff', sample_data)
                lines.append(f"{count:12d} | {x:8.3f} | {y:8.3f} | {z:8.3f}")
                count_read += 1
            
            if head_n and count_read >= head_n:
                break

    print(f"Reading ADXL Batch file: {filepath}\n")
    print(f"{'Count':>12} | {'X (g)':>8} | {'Y (g)':>8} | {'Z (g)':>8}")
    print("-" * 45)
    
    if head_n:
        lines = list(lines)[:head_n]
        
    for line in lines:
        print(line)

def read_bogie(filepath, tail_n, head_n):
    lines = deque(maxlen=tail_n) if tail_n else []
    count_read = 0
    with open(filepath, "rb") as f:
        while True:
            header = f.read(9)
            if len(header) < 9: break
            start_count, sample_count = struct.unpack('<QB', header)
            
            for _ in range(sample_count):
                sample_data = f.read(44)
                if len(sample_data) < 44: break
                data = struct.unpack('<Qfffffffff', sample_data)
                count = data[0]
                floats = data[1:]
                f_str = " | ".join([f"{val:8.3f}" for val in floats])
                lines.append(f"{count:12d} | {f_str}")
                count_read += 1
                
            if head_n and count_read >= head_n:
                break

    print(f"Reading Bogie Batch file: {filepath}\n")
    print(f"{'Count':>12} | {'IIS_X':>8} | {'IIS_Y':>8} | {'IIS_Z':>8} | {'IMU_X':>8} | {'IMU_Y':>8} | {'IMU_Z':>8} | {'GYR_X':>8} | {'GYR_Y':>8} | {'GYR_Z':>8}")
    print("-" * 115)
    
    if head_n:
        lines = list(lines)[:head_n]
        
    for line in lines:
        print(line)

def read_encoder(filepath, tail_n, head_n):
    lines = deque(maxlen=tail_n) if tail_n else []
    count_read = 0
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(20)
            if len(chunk) < 20: break
            
            count, idx, pos, spd, direct, flags = struct.unpack('<QIihbB', chunk)
            lines.append(f"{count:12d} | {idx:8d} | {pos:10d} | {spd:7d} | {direct:4d} | 0x{flags:02X}")
            count_read += 1
            
            if head_n and count_read >= head_n:
                break

    print(f"Reading Encoder file: {filepath}\n")
    print(f"{'Count':>12} | {'EventIdx':>8} | {'Pos(mm)':>10} | {'Spd(cK)':>7} | {'Dir':>4} | {'Flags':>5}")
    print("-" * 65)
    
    for line in lines:
        print(line)

def main():
    parser = argparse.ArgumentParser(description="Read UABAMS Gateway raw binary files.")
    parser.add_argument("type", choices=["adxl", "bogie", "encoder"], help="Type of file to read")
    parser.add_argument("filepath", help="Path to the .bin file")
    parser.add_argument("-n", "--tail", type=int, default=None, help="Show only the last N records")
    parser.add_argument("-H", "--head", type=int, default=None, help="Show only the first N records")
    
    args = parser.parse_args()
    
    if args.tail and args.head:
        print("Warning: Cannot use both --tail and --head at the same time. Using --head.")
        args.tail = None
        
    if not os.path.exists(args.filepath):
        print(f"Error: File '{args.filepath}' not found.")
        sys.exit(1)
        
    if args.type == "adxl":
        read_adxl(args.filepath, args.tail, args.head)
    elif args.type == "bogie":
        read_bogie(args.filepath, args.tail, args.head)
    elif args.type == "encoder":
        read_encoder(args.filepath, args.tail, args.head)

if __name__ == '__main__':
    main()
