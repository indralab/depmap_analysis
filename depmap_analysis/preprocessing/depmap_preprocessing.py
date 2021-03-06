import logging
import argparse
from typing import Dict
from pathlib import Path

import pandas as pd

from depmap_analysis.util import io_functions as io
from depmap_analysis.network_functions.famplex_functions import ns_id_xref, \
    ns_id_to_name

logger = logging.getLogger(__name__)
__all__ = ['run_corr_merge', 'drugs_to_corr_matrix', 'get_mitocarta_info']


def run_corr_merge(crispr_raw=None, rnai_raw=None,
                   crispr_corr=None, rnai_corr=None,
                   output_dir='correlation_output',
                   remove_self_corr=True, dropna=False, random_sampl=0):
    """Return a merged correlation matrix from DepMap data

    Start with with either the raw DepMap files or pre-calculated
    correlation matrices

    Parameters
    ----------
    crispr_raw : str|pd.DataFrame
        Path to the raw crispr data. This file is typically named
        'Achilles_gene_effect.csv' at the DepMap portal.
    rnai_raw : str|pd.DataFrame
        Path to the raw RNAi data. This file is typically named
        'D2_combined_gene_dep_scores.csv'
    crispr_corr : str|pd.DataFrame
        Path to the pre-calculated crispr data matrix. This data structure
        is the result from running `crispr_raw_df.corr()`.
    rnai_corr : str|pd.DataFrame
        Path to the pre-calculated rnai data matrix. This data structure
        is the result from running `rnai_raw_df.corr()`.
    output_dir : str
        If used, write the correlation matrices to this directory.
        Otherwise they will be written to the same directory as the raw
        input data.
    remove_self_corr : bool
        If True, remove self correlations from the resulting DataFrame.
        Default: True
    random_sampl : int
        If specified, provides the size of the final correlation matrix
        where the genes are picked at random from the intersection of genes
        from both the RNAI and CRISPR data sets.
    dropna : bool
        If True, return the result of
        corr_df.dropna(axis=0, how='all').dropna(axis=1, how='all')
        Default: False.

    Returns
    -------
    pd.DataFrame
        A data frame containing the combined z-score matrix with NaN's
        removed.
    """
    if crispr_raw is None and crispr_corr is None:
        raise ValueError('Need to provide one of crispr_raw or cripsr_corr')
    if rnai_raw is None and rnai_corr is None:
        raise ValueError('Need to provide one of rnai_raw or rnai_corr')

    # First check for correlation matrix, then get it if it doesn't exist
    if crispr_corr:
        if isinstance(crispr_corr, str):
            logger.info(f'Reading crispr correlations from file {crispr_corr}')
            crispr_corr_df = pd.read_hdf(crispr_corr)
        else:
            crispr_corr_df = crispr_corr
    else:
        # Create new one, write to input file's directory
        if isinstance(crispr_raw, str):
            logger.info(f'Reading raw DepMap data from {crispr_raw}')
            crispr_raw_df = io.file_opener(crispr_raw, index_col=0)
        else:
            crispr_raw_df = crispr_raw
        crispr_corr_df = raw_depmap_to_corr(crispr_raw_df, split_names=True,
                                            dropna=True)

        crispr_fpath = Path(output_dir).joinpath('_crispr_all_correlations.h5')
        logger.info(f'Saving crispr correlation matrix to {crispr_fpath}')
        if not crispr_fpath.parent.is_dir():
            crispr_fpath.parent.mkdir(parents=True, exist_ok=True)
        crispr_corr_df.to_hdf(crispr_fpath.absolute(), 'corr')

    if rnai_corr:
        if isinstance(rnai_corr, str):
            logger.info(f'Reading rnai correlations from file {crispr_corr}')
            rnai_corr_df = pd.read_hdf(rnai_corr)
        else:
            rnai_corr_df = rnai_corr
    else:
        # Create new one, write to input file's directory
        if isinstance(rnai_raw, str):
            logger.info(f'Reading raw DepMap data from {rnai_raw}')
            rnai_raw_df = io.file_opener(rnai_raw, index_col=0)
        else:
            rnai_raw_df = rnai_raw

        # Check if we need to transpose the df
        if len(set(crispr_corr_df.columns.values) &
               set([n.split()[0] for n in rnai_raw_df.columns])) == 0:
            logger.info('Transposing RNAi raw data dataframe...')
            rnai_raw_df = rnai_raw_df.T

        rnai_corr_df = raw_depmap_to_corr(rnai_raw_df, split_names=True,
                                          dropna=True)

        rnai_fpath = Path(output_dir).joinpath('_rnai_all_correlations.h5')
        if not rnai_fpath.parent.is_dir():
            rnai_fpath.mkdir(parents=True, exist_ok=True)
        logger.info(f'Saving rnai correlation matrix to {rnai_fpath}')
        rnai_corr_df.to_hdf(rnai_fpath.absolute().as_posix(), 'corr')

    # Merge the correlation matrices
    z_cm = merge_corr_df(crispr_corr_df, rnai_corr_df,
                         remove_self_corr, dropna)

    if random_sampl and random_sampl < len(z_cm.columns):
        # Get n random rows
        z_cm = z_cm.sample(n=random_sampl)

        # Make square
        z_cm = z_cm[list(z_cm.index.values)]

    assert z_cm.notna().sum().sum() > 0, 'Correlation matrix is empty'

    return z_cm


def drugs_to_corr_matrix(raw_file: str, info_file: str):
    """Preprocess and create a correlation matrix from raw drug data

    Parameters
    ----------
    raw_file : str
        Path to DepMap PRISM drug repurposing data file. Should match
        primary-screen-replicate-collapsed-logfold-change.csv
    info_file : str
        Path to DepMap PRISM drug repurposing info file. Should match
        primary-screen-replicate-collapsed-treatment-info.csv
    """
    def _get_drug_name(drug_id):
        drug_rec = info_df.loc[drug_id]
        return drug_rec['name']

    raw_df: pd.DataFrame = io.file_opener(raw_file, index_col=0)
    info_df: pd.DataFrame = io.file_opener(info_file, index_col=0)
    col_names = [_get_drug_name(did) for did in raw_df.columns]
    raw_df.columns = col_names

    return raw_depmap_to_corr(raw_df)


def raw_depmap_to_corr(depmap_raw_df: pd.DataFrame,
                       split_names: bool = False,
                       dropna: bool = False):
    """Pre-process and create a correlation matrix

    Any multi indexing is removed. Duplicated columns are also removed.

    Parameters
    ----------
    depmap_raw_df : pd.DataFrame
        The raw data from the DepMap portal as a pd.DataFrame
    split_names : bool
        If True, check if column names contain whitespace and if the do,
        split the name and keep the first part.
    dropna : bool
        If True, drop nan columns (should be genes) before calculating the
        correlations

    Returns
    -------
    corr : pd.DataFrame
        A pd.DataFrame containing the pearson correlations of the raw data.
    """
    # Rename
    if split_names and len(depmap_raw_df.columns[0].split()) > 1:
        logger.info('Renaming columns to contain only first part of name')
        gene_names = [n.split()[0] for n in depmap_raw_df.columns]
        depmap_raw_df.columns = gene_names

    # Drop duplicates
    depmap_raw_df = depmap_raw_df.loc[:, ~depmap_raw_df.columns.duplicated()]

    # Drop nan's
    if dropna:
        logger.info('Dropping nan columns (axis=1)')
        depmap_raw_df = depmap_raw_df.dropna(axis=1)

    # Calculate correlation
    logger.info('Calculating data correlation matrix. This can take up to '
                '10 min depending on the size of the dataframe.')
    corr = depmap_raw_df.corr()
    logger.info('Done calculating data correlation matrix.')
    return corr


def merge_corr_df(corr_df, other_corr_df, remove_self_corr=True,
                  dropna=False):
    """Merge two correlation matrices containing their combined z-scores

    Parameters
    ----------
    corr_df : pd.DataFrame
        A square pandas DataFrame containing gene-gene correlation values
        to be merged with other_corr_df.
    other_corr_df : pd.DataFrame
        A square pandas DataFrame containing gene-gene correlation values
        to be merged with corr_df.
    remove_self_corr : bool
        If True, remove self correlations from the resulting DataFrame.
        Default: True
    dropna : bool
        If True, return the result of
        corr_df.dropna(axis=0, how='all').dropna(axis=1, how='all')
        Default: False.

    Returns
    -------
    pd.DataFrame
        A merged correlation matrix containing the merged values a z-scores
    """
    def _rename(corr):
        gene_names = [n.split()[0] for n in corr.columns]
        corr.columns = gene_names
        corr.index = gene_names
        return corr

    def _z_scored(corr):
        mean = corr.values.mean()
        sd = corr.values.std()
        logger.info('Mean value: %f; St dev: %f' % (mean, sd))
        return (corr - mean) / sd

    # Rename columns/indices to gene name only
    if len(corr_df.columns[0].split()) > 1:
        corr_df = _rename(corr_df)
    if len(other_corr_df.columns[0].split()) > 1:
        other_corr_df = _rename(other_corr_df)

    # Get corresponding z-score matrices
    logger.info('Getting z-score matrix of first data frame.')
    corr_z = _z_scored(corr_df)
    logger.info('Getting z-score matrix of second data frame.')
    other_z = _z_scored(other_corr_df)

    # Merge
    dep_z = (corr_z + other_z) / 2
    if remove_self_corr:
        # Assumes the max correlation ONLY occurs on the diagonal
        self_corr_value = dep_z.loc[dep_z.columns[0], dep_z.columns[0]]
        dep_z = dep_z[dep_z != self_corr_value]
    if dropna:
        dep_z = dep_z.dropna(axis=0, how='all').dropna(axis=1, how='all')
    assert dep_z.notna().sum().sum() > 0, print('Correlation matrix is empty')
    return dep_z


def get_mitocarta_info(mitocarta_file: str) -> Dict[str, str]:
    """Load mitocarta file and get mapping of gene to gene function

    Parameters
    ----------
    mitocarta_file : str
        Path as string to mitocarta file

    Returns
    -------
    Dict[str, str]

    """
    xls = pd.ExcelFile(mitocarta_file)
    # Sheet A is second sheet of MitoCarta 3.0 info
    sheet_a = xls.parse(xls.sheet_names[1])
    hgnc_expl = {}
    for eid, expl in zip(sheet_a.HumanGeneID.values,
                         sheet_a.Description.values):
        hgnc_tup = ns_id_xref(from_ns='EGID', from_id=eid, to_ns='HGNC')
        if hgnc_tup:
            hgnc_symb = ns_id_to_name(*hgnc_tup)
            if hgnc_symb:
                hgnc_expl[hgnc_symb] = expl
    return hgnc_expl


if __name__ == '__main__':
    parser = argparse.ArgumentParser('DepMap Data pre-processing')
    # Input dirs
    parser.add_argument('--crispr-raw',
                        help='The raw CRISPR gene effect data. File name '
                             'should match *gene_effect.csv')
    parser.add_argument('--rnai-raw',
                        help='The raw RNAi gene dependency data. File name '
                             'should match *gene_dep_scores.csv')
    # Option to start from raw correlations matrix instead of running
    # correlation calculation directly
    parser.add_argument('--crispr-corr',
                        help='The file containing an hdf compressed '
                             'correlation data frame of the crispr data. If '
                             'this file is provided, the raw crispr data is '
                             'ignored.')
    parser.add_argument('--rnai-corr',
                        help='The file containing an hdf compressed '
                             'correlation data frame of the rnai data. If '
                             'this file is provided, the raw rnai data is '
                             'ignored.')
    # Output dirs
    parser.add_argument('--output-dir', '-o',
                        help='Optional. A directory where to put the '
                             'output of the script. If not provided the '
                             'ouput will go to the directory/ies where '
                             'the corresponding input came from, i.e. the '
                             'output from the RNAi input will be placed '
                             'in the RNAi input directory. The combined '
                             'z-score matrix will be written to the crispr '
                             'input directory if this option is not '
                             'provided.')
    parser.add_argument('--random', '-r', type=int,
                        help='Optional. If specified, provide the size of '
                             'the final correlation matrix where the genes '
                             'are picked at random from the intersection of '
                             'genes from both the RNAI and CRISPR data sets.')
    parser.add_argument('--fname', help='A file name for the output '
                                        'correlations DataFrame.')

    args = parser.parse_args()
    options = {
        'crispr_raw': args.crispr_raw,
        'rnai_raw': args.rnai_raw,
        'crispr_corr': args.crispr_corr,
        'rnai_corr': args.rnai_corr,
        'output_dir': args.output_dir,
        'random_sampl': args.random
    }

    # Run main script
    z_corr = run_corr_merge(**options)

    # Write merged correlations combined z score
    outdir = args.output_dir if args.output_dir else (Path(
        args.crispr_corr).parent.as_posix() if args.crispr_corr else Path(
        args.crispr_raw).parent.as_posix())
    logger.info(f'Writing combined correlations to {outdir}')
    fname = args.fname if args.fname else 'combined_z_score.h5'
    z_corr.to_hdf(Path(outdir, fname), 'zsc')
