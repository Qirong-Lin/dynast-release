import copy
from functools import partial
from operator import truediv

import numpy as np
import pandas as pd

from .. import utils
from ..logging import logger
from .conversion import CONVERSION_COMPLEMENT, CONVERSIONS_PARSER

SNP_COLUMNS = ['contig', 'genome_i', 'original', 'converted']


def read_snps(snps_path):
    """Read SNPs CSV as a dictionary

    :param snps_path: path to SNPs CSV
    :type snps_path: str

    :return: dictionary of contigs as keys and sets of genomic positions with SNPs as values
    :rtype: dictionary
    """
    df = pd.read_csv(
        snps_path,
        dtype={
            'contig': 'category',
            'genome_i': np.uint32,
            'original': 'category',
            'converted': 'category',
        }
    )
    df['conversion'] = df['original'].astype(str) + df['converted'].astype(str)
    df.drop(columns=['original', 'converted'], inplace=True)
    snps = {}
    for (conversion, contig), indices in dict(df.groupby(['conversion', 'contig'], sort=False,
                                                         observed=True).agg(set)['genome_i']).items():
        snps.setdefault(conversion, {})[contig] = indices
    return snps


def read_snp_csv(snp_csv):
    """Read a user-provided SNPs CSV

    :param snp_csv: path to SNPs CSV
    :type snp_csv: str

    :return: dictionary of contigs as keys and sets of genomic positions with SNPs as values
    :rtype: dictionary
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


def extract_conversions_part(conversions_path, counter, lock, index, alignments=None, quality=27, update_every=5000):
    """Extract number of conversions for every genomic position.

    :param conversions_path: path to conversions CSV
    :type conversions_path: str
    :param counter: counter that keeps track of how many reads have been processed
    :type counter: multiprocessing.Value
    :param lock: semaphore for the `counter` so that multiple processes do not
                 modify it at the same time
    :type lock: multiprocessing.Lock
    :param index: list of (file position, number of lines) tuples to process
    :type index: list
    :param alignments: set of (read_id, alignment_index) tuples to process. All
        alignments are processed if this option is not provided.
    :type alignments: set, optional
    :param quality: only count conversions with PHRED quality greater than this value,
                    defaults to `27`
    :type quality: int, optional
    :param update_every: update the counter every this many reads, defaults to `5000`
    :type update_every: int, optional

    :return: nested dictionary that contains number of conversions for each contig and position
    :rtype: dictionary
    """
    convs = {}
    n = 0
    with open(conversions_path, 'r') as f:
        for pos, n_lines, _ in index:
            f.seek(pos)
            n += 1
            if n == update_every:
                lock.acquire()
                counter.value += update_every
                lock.release()
                n = 0

            for _ in range(n_lines):
                line = f.readline()
                groups = CONVERSIONS_PARSER.match(line).groupdict()
                key = (groups['read_id'], int(groups['index']))
                if alignments and key not in alignments:
                    break

                if int(groups['quality']) > quality:
                    conversion = f'{groups["original"]}{groups["converted"]}'

                    contig = groups['contig']
                    genome_i = int(groups['genome_i'])
                    count = convs.setdefault(conversion, {}).setdefault(contig, {}).setdefault(genome_i, 0)
                    convs[conversion][contig][genome_i] = count + 1
    lock.acquire()
    counter.value += n
    lock.release()
    if alignments:
        del alignments

    return convs


def extract_conversions(conversions_path, index_path, alignments=None, quality=27, conversions=None, n_threads=8):
    """Wrapper around `extract_conversions_part` that works in parallel

    :param conversions_path: path to conversions CSV
    :type conversions_path: str
    :param index_path: path to conversions index
    :type index_path: str
    :param alignments: set of (read_id, alignment_index) tuples to process. All
        alignments are processed if this option is not provided.
    :type alignments: set, optional
    :param quality: only count conversions with PHRED quality greater than this value,
                    defaults to `27`
    :type quality: int, optional
    :param n_threads: number of threads, defaults to `8`
    :type n_threads: int, optional

    :return: nested dictionary that contains number of conversions for each contig and position
    :rtype: dictionary
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
    conversions_path,
    index_path,
    coverage,
    snps_path,
    alignments=None,
    quality=27,
    threshold=0.5,
    min_coverage=1,
    n_threads=8,
):
    """Detect SNPs.

    :param conversions_path: path to conversions CSV
    :type conversions_path: str
    :param index_path: path to conversions index
    :type index_path: str
    :param coverage: dictionary containing genomic coverage
    :type coverage: dict
    :param snps_path: path to output SNPs
    :type snps_path: str
    :param alignments: set of (read_id, alignment_index) tuples to process. All
        alignments are processed if this option is not provided.
    :type alignments: set, optional
    :param quality: only count conversions with PHRED quality greater than this value,
                    defaults to `27`
    :type quality: int, optional
    :param threshold: positions with conversions / coverage > threshold will be
                      considered as SNPs, defaults to `0.5`
    :type threshold: float, optional
    :param min_coverage: only positions with at least this many mapping read_snps
                         are considered, defaults to `1`
    :type min_coverage: int, optional
    :param n_threads: number of threads, defaults to `8`
    :type n_threads: int, optional
    """
    logger.debug('Counting number of conversions for each genomic position')
    convs = extract_conversions(
        conversions_path, index_path, alignments=alignments, quality=quality, n_threads=n_threads
    )

    logger.debug(f'Writing detected SNPs to {snps_path}')
    with open(snps_path, 'w') as f:
        f.write('contig,genome_i,original,converted\n')
        for conversion, _convs in convs.items():
            fractions = utils.merge_dictionaries(_convs, coverage, f=truediv)
            for (contig, genome_i), fraction in utils.flatten_dictionary(fractions):
                # If (# conversions) / (# coverage) is greater than a threshold,
                # consider this a SNP and write to CSV
                if coverage[contig][genome_i] >= min_coverage and fraction > threshold:
                    f.write(f'{contig},{genome_i},{conversion[0]},{conversion[1]}\n')

    return snps_path
