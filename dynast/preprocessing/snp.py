import copy
import multiprocessing
from functools import partial
from operator import truediv
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .. import utils
from ..logging import logger
from .conversion import CONVERSION_COMPLEMENT, CONVERSIONS_PARSER

SNP_COLUMNS = ['contig', 'genome_i', 'conversion']


def read_snps(snps_path: str) -> Dict[str, Dict[str, Set[int]]]:
    """Read SNPs CSV as a dictionary

    Args:
        snps_path: Path to SNPs CSV

    Returns:
        Dictionary of contigs as keys and sets of genomic positions with SNPs as values
    """
    df = pd.read_csv(
        snps_path, dtype={
            'contig': 'category',
            'genome_i': np.uint32,
            'conversion': 'category',
        }
    )
    snps = {}
    for (conversion, contig), indices in dict(df.groupby(['conversion', 'contig'], sort=False,
                                                         observed=True)['genome_i'].agg(set)).items():
        snps.setdefault(conversion, {})[contig] = indices
    return snps


def read_snp_csv(snp_csv: str) -> Dict[str, Dict[str, Set[int]]]:
    """Read a user-provided SNPs CSV

    Args:
        snp_csv: Path to SNPs CSV

    Returns:
        Dictionary of contigs as keys and sets of genomic positions with SNPs as values
    """
    # Check if this is a generic SNP csv (with two columns) or one generated by
    # dynast (4 columns)
    with open(snp_csv, 'r') as f:
        line = f.readline().strip()
        columns = line.split(',')
        if columns == SNP_COLUMNS:
            return read_snps(snp_csv)
        elif len(columns) == 2:
            header = False
            try:
                int(columns[-1])
            except ValueError:
                header = True
            df = pd.read_csv(
                snp_csv,
                names=['contig', 'genome_i'],
                skiprows=1 if header else None,
                dtype={
                    'contig': 'category',
                    'genome_i': np.uint32
                }
            )
            _snps = dict(df.groupby('contig', sort=False, observed=True).agg(set)['genome_i'])
            snps = {}
            for conversion in CONVERSION_COMPLEMENT.keys():
                snps[conversion] = copy.deepcopy(_snps)
            return snps
        else:
            raise Exception(f'Failed to parse {snp_csv}')


def extract_conversions_part(
    conversions_path: str,
    counter: multiprocessing.Value,
    lock: multiprocessing.Lock,
    index: List[Tuple[int, int, int]],
    alignments: Optional[List[Tuple[str, int]]] = None,
    conversions: Optional[FrozenSet[str]] = None,
    quality: int = 27,
    update_every: int = 5000
) -> Dict[str, Dict[str, Dict[int, int]]]:
    """Extract number of conversions for every genomic position.

    Args:
        conversions_path: Path to conversions CSV
        counter: Counter that keeps track of how many reads have been processed
        lock: Semaphore for the `counter` so that multiple processes do not
            modify it at the same time
        index: Conversions index
        alignments: Set of (read_id, alignment_index) tuples to process. All
            alignments are processed if this option is not provided.
        conversions: Set of conversions to consider
        quality: Only count conversions with PHRED quality greater than this value
        update_every: Update the counter every this many reads

    Returns:
        Nested dictionary that contains number of conversions for each contig and position
    """
    convs = {}
    n = 0
    with open(conversions_path, 'r') as f:
        for pos, n_lines, _ in index:
            f.seek(pos)
            n += 1
            if n == update_every:
                with lock:
                    counter.value += update_every
                n = 0

            for _ in range(n_lines):
                line = f.readline()
                groups = CONVERSIONS_PARSER.match(line).groupdict()
                key = (groups['read_id'], int(groups['index']))
                if alignments and key not in alignments:
                    break

                if int(groups['quality']) > quality:
                    conversion = groups["conversion"]
                    if conversions and conversion not in conversions:
                        continue

                    contig = groups['contig']
                    genome_i = int(groups['genome_i'])
                    count = convs.setdefault(conversion, {}).setdefault(contig, {}).setdefault(genome_i, 0)
                    convs[conversion][contig][genome_i] = count + 1
    with lock:
        counter.value += n
    if alignments:
        del alignments

    return convs


def extract_conversions(
    conversions_path: str,
    index_path: str,
    alignments: Optional[List[Tuple[str, int]]] = None,
    conversions: Optional[FrozenSet[str]] = None,
    quality: int = 27,
    n_threads: int = 8
) -> Dict[str, Dict[str, Dict[int, int]]]:
    """Wrapper around `extract_conversions_part` that works in parallel

    Args:
        conversions_path: Path to conversions CSV
        index_path: Path to conversions index
        alignments: Set of (read_id, alignment_index) tuples to process. All
            alignments are processed if this option is not provided.
        conversions: Set of conversions to consider
        quality: Only count conversions with PHRED quality greater than this value
        n_threads: Number of threads

    Returns:
        Nested dictionary that contains number of conversions for each contig and position
    """
    logger.debug(f'Loading index {index_path} for {conversions_path}')
    index = utils.read_pickle(index_path)

    logger.debug(f'Splitting index into {n_threads} parts')
    parts = utils.split_index(index, n=n_threads)

    logger.debug(f'Spawning {n_threads} processes')
    pool, counter, lock = utils.make_pool_with_counter(n_threads)
    async_result = pool.starmap_async(
        partial(
            extract_conversions_part,
            conversions_path,
            counter,
            lock,
            alignments=alignments,
            conversions=conversions,
            quality=quality,
        ), [(part,) for part in parts]
    )
    pool.close()

    # Display progres bar
    utils.display_progress_with_counter(counter, len(index), async_result)
    pool.join()

    logger.debug('Combining conversions')
    convs = {}
    for conversions_part in async_result.get():
        convs = utils.merge_dictionaries(convs, conversions_part)

    return convs


def detect_snps(
    conversions_path: str,
    index_path: str,
    coverage: Dict[str, Dict[int, int]],
    snps_path: str,
    alignments: Optional[List[Tuple[str, int]]] = None,
    conversions: Optional[FrozenSet[str]] = None,
    quality: int = 27,
    threshold: float = 0.5,
    min_coverage: int = 1,
    n_threads: int = 8,
) -> str:
    """Detect SNPs.

    Args:
        conversions_path: Path to conversions CSV
        index_path: Path to conversions index
        coverage: Dictionary containing genomic coverage
        snps_path: Path to output SNPs
        alignments: Set of (read_id, alignment_index) tuples to process. All
            alignments are processed if this option is not provided.
        conversions: Set of conversions to consider
        quality: Only count conversions with PHRED quality greater than this value
        threshold: Positions with conversions / coverage > threshold will be
            considered as SNPs
        min_coverage: Only positions with at least this many mapping read_snps
            are considered
        n_threads: Number of threads

    Returns:
        Path to SNPs CSV
    """
    logger.debug('Counting number of conversions for each genomic position')
    convs = extract_conversions(
        conversions_path,
        index_path,
        alignments=alignments,
        conversions=conversions,
        quality=quality,
        n_threads=n_threads
    )

    logger.debug(f'Writing detected SNPs to {snps_path}')
    with open(snps_path, 'w') as f:
        f.write(f'{",".join(SNP_COLUMNS)}\n')
        for conversion, _convs in convs.items():
            fractions = utils.merge_dictionaries(_convs, coverage, f=truediv)
            for (contig, genome_i), fraction in utils.flatten_dictionary(fractions):
                # If (# conversions) / (# coverage) is greater than a threshold,
                # consider this a SNP and write to CSV
                if coverage[contig][genome_i] >= min_coverage and fraction > threshold:
                    f.write(f'{contig},{genome_i},{conversion}\n')

    return snps_path
