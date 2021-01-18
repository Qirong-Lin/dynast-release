import os
import platform

PACKAGE_PATH = os.path.dirname(__file__)
PLATFORM = platform.system().lower()
BINS_DIR = os.path.join(PACKAGE_PATH, 'bins')
WHITELIST_DIR = os.path.join(PACKAGE_PATH, 'whitelists')
MODELS_DIR = os.path.join(PACKAGE_PATH, 'models')
MODEL_PATH = os.path.join(MODELS_DIR, 'pi.stan')
MODEL_NAME = 'pi'

RE_CHOICES = ['index', 'parse', 'snp', 'count', 'aggregate', 'p', 'pi', 'split']
GROUP_CHOICES = ['barcode', 'GX']
RECOMMENDED_MEMORY = 16 * (1024**3)  # 16 GB

# Common arguments for all STAR runs
STAR_ARGUMENTS = {
    '--outSAMmultNmax': 1,
    '--outSAMtype': ['BAM', 'SortedByCoordinate'],
    '--outSAMattributes': ['NH', 'HI', 'AS', 'NM', 'nM', 'MD', 'GX', 'GN'],
    '--bamRemoveDuplicatesType': 'UniqueIdentical',
    '--outFilterScoreMinOverLread': 0.3,
    '--outFilterMatchNminOverLread': 0.3,
}

# Additional arguments for STARsolo runs
STAR_SOLO_ARGUMENTS = {
    '--outSAMattributes': ['CR', 'CY', 'UR', 'UY', 'CB', 'UB', 'sS', 'sQ', 'sM'],
    '--soloFeatures': ['Gene', 'Velocyto'],
    '--soloCBwhitelist': 'None',
}

NASC_ARGUMENTS = {
    '--soloStrand': 'Forward',
    '--alignSJoverhangMin': 1000,
    '--alignSJDBoverhangMin': 1,
    '--outFilterMismatchNoverReadLmax': 1,
    '--outFilterMismatchNmax': 10,
    '--outFilterMismatchNoverLmax': 0.1,
    '--outFilterScoreMinOverLread': 0.66,
    '--outFilterMatchNminOverLread': 0.66,
    '--scoreDelOpen': -10000,
    '--scoreInsOpen': -10000,
}

BAM_PEEK_READS = 100000
BAM_REQUIRED_TAGS = ['MD', 'GX']
