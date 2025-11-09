import time
import random

# simulate work delay (so jobs can overlap if locking fails)
time.sleep(random.uniform(0.5, 1.5))

with open("shared_counter.txt", "r+") as f:
    value = int(f.read().strip())
    value += 1
    f.seek(0)
    f.write(str(value))
    f.truncate()

