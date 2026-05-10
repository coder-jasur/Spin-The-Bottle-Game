
import os
import json

bundle_path = r'c:\Users\dhikm\PycharmProjects\SpinTheBootleGame\src\app\site\bottle\bundle\300'
s_files = [f[:-5] for f in os.listdir(bundle_path) if f.startswith('s_') and f.endswith('.json')]

missing_spines = [
    'crown1', 'air_kiss', 'gem', 'strawberry', 'tomato', 'flower', 
    'icecream', 'bosscap', 'weddingring1', 'rosep', 'roser', 'rosew', 
    'trout', 'cola', 'cognac'
]

print("Searching for matches on disk:")
for ms in missing_spines:
    matches = [s for s in s_files if ms in s]
    # Also check if g_ms exists and matches s_something
    # e.g. gem uses g_gem1_v2, maybe s_gem5 is a match
    print(f" - {ms}: {matches}")

# Check specific ones
print("\nSpecific checks:")
print(f"s_rose_v2 in s_files: {'s_rose_v2' in s_files}")
print(f"s_gem5 in s_files: {'s_gem5' in s_files}")
