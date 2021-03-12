import uproot
import awkward as ak
import numpy as np

import time
import gc
import argparse
import yaml
import json
import collections
from glob import glob
# from memory_profiler import profile

########################################################################

def nested_dict():
    return collections.defaultdict(nested_dict)

def compute_mean(sums, counts, aggregate=True, *file_range):
    if aggregate:
        if file_range:
            if len(file_range) == 2 and file_range[0] <= file_range[1]:
                return sums[file_range[0]:file_range[1]].sum()/counts[file_range[0]:file_range[1]].sum()
            else:
                raise ValueError("file range should have 2 values with a[0] <= a[1]")
        else:
            return sums.sum()/counts.sum()
    else:
        return sums/counts

def compute_std(sums, sums2, counts, aggregate=True, *file_range):
    if aggregate:
        if file_range:
            if len(file_range) == 2 and file_range[0] <= file_range[1]:
                average2 = sums2[file_range[0]:file_range[1]].sum()/counts[file_range[0]:file_range[1]].sum()
                average = sums[file_range[0]:file_range[1]].sum()/counts[file_range[0]:file_range[1]].sum()
                return np.sqrt(average2 - average**2)
            else:
                raise ValueError("file range should have 2 values with a[0] <= a[1]")
        else:
            return np.sqrt(sums2.sum()/counts.sum() - (sums.sum()/counts.sum())**2)
    else:
        return np.sqrt(sums2/counts - (sums/counts)**2)

def compute_scaling(file_name, file_i, tree_name, features_dict, grid_selection_dict, sums, sums2, counts, means_stds, log_step, version):
    with uproot.open(file_name, array_cache='5 GB') as f:
        if len(f.keys()) == 0:
            return 0
        else:
            for var_type, var_dict in features_dict.items():
                for var, (selection, aliases) in var_dict.items():
                    for grid_type, grid_cut in grid_selection_dict[var_type].items():
                        if selection == None:
                            cut = grid_cut
                        elif grid_cut == None:
                            cut = None
                        else:
                            cut = f'{selection} & {grid_cut}'
                        taus_batches = f[tree_name].iterate(var, cut=cut, aliases=aliases, step_size='1 GB') # , how='zip'
                        for batch in taus_batches:
                            sums[var_type][var][grid_type][file_i] += ak.sum(batch[var])
                            sums2[var_type][var][grid_type][file_i] += ak.sum(batch[var]**2)
                            counts[var_type][var][grid_type][file_i] += ak.count(batch[var])
                            del(batch)
                        if file_i%log_step == 0:
                            mean = compute_mean(sums[var_type][var][grid_type], counts[var_type][var][grid_type], aggregate=True)
                            std = compute_std(sums[var_type][var][grid_type], sums2[var_type][var][grid_type], counts[var_type][var][grid_type], aggregate=True)
                            means_stds[var_type][var][grid_type] = {'mean': mean, 'std': std}
            if file_i%log_step == 0:
                with open(f'output/means_stds_v{version}_log_{file_i//log_step}.json', 'w') as fout:
                    json.dump(means_stds, fout)
            return 1


########################################################################

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("cfg", type=str, help="path to yaml configuration file")
    args = parser.parse_args()
    with open(args.cfg) as f:
        scaling_dict = yaml.load(f, Loader=yaml.FullLoader)

    # read cfg parameters
    setup_dict = scaling_dict['setup']
    features_dict = scaling_dict['features']
    file_path = setup_dict['file_path']
    file_range = setup_dict['file_range']
    tree_name = setup_dict['tree_name']
    log_step = setup_dict['log_step']
    version = setup_dict['version']
    selection_dict = setup_dict['selection']
    grid_selection_dict = setup_dict['grid_selection']
    assert log_step > 0 and type(log_step) == int
    assert len(file_range)==2 and file_range[0]<=file_range[1]
    file_names = sorted(glob(file_path))[file_range[0]:file_range[1]]
    print(f'\n[INFO] will process {len(file_names)} input files from {file_path}')
    print(f'[INFO] will dump means & stds to json after every {log_step} files')

    # initialize sums and counts
    sums, sums2, counts, means_stds = nested_dict(), nested_dict(), nested_dict(), nested_dict()
    for var_type, var_dict in features_dict.items():
        for var in var_dict.keys():
            for grid_type in grid_selection_dict[var_type].keys():
                sums[var_type][var][grid_type] = np.zeros(len(file_names), dtype='float64')
                sums2[var_type][var][grid_type] = np.zeros(len(file_names), dtype='float64')
                counts[var_type][var][grid_type] = np.zeros(len(file_names), dtype='int64')
    print('[INFO] starting to accumulate sums & counts:\n')
    skip_counter = 0
    program_starts = time.time()
    last_file_done = program_starts
    # loop over input files
    for file_i, file_name in enumerate(file_names):
        scaling_status = compute_scaling(file_name, file_i, tree_name, features_dict, grid_selection_dict, sums, sums2, counts, means_stds, log_step, version)
        gc.collect()
        if scaling_status == 0: # there is no objects in the file
            print(f'[WARNING] couldn\'t find any object in {file_name}: skipping the file')
            skip_counter += 1
            last_file_done = time.time()
            continue
        else:
            processed_file = time.time()
            print(f'---> processed {file_name} in {processed_file - last_file_done:.2f} s')
            last_file_done = processed_file
    # calculate scaling parameters from the final sums/sums2/counts dicts
    for var_type, var_dict in features_dict.items():
        for var in var_dict.keys():
            for grid_type in grid_selection_dict[var_type].keys():
                mean = compute_mean(sums[var_type][var][grid_type], counts[var_type][var][grid_type], aggregate=True)
                std = compute_std(sums[var_type][var][grid_type], sums2[var_type][var][grid_type], counts[var_type][var][grid_type], aggregate=True)
                means_stds[var_type][var][grid_type] = {'mean': mean, 'std': std}
    # save to json
    with open(f'output/means_stds_v{version}.json', 'w') as fout:
        json.dump(means_stds, fout)
        print(f'saved the scaling parameters to: output/means_stds_v{version}.json')
    if skip_counter > 0:
        print(f'[WARNING] during the processing {skip_counter} files with no objects were skipped ')
