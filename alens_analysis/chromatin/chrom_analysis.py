#!/usr/bin/env python

"""@package docstring
File: chrom_analysis.py
Author: Adam Lamson
Email: alamson@flatironinstitute.org
Description:
"""
# Basic useful imports
import yaml
import h5py
from copy import deepcopy
from time import time
from functools import reduce


# Data manipulation
import numpy as np
import torch
import scipy.stats as stats
from scipy.signal import savgol_filter

# Clustering stuff
from itertools import cycle

from ..helpers import contiguous_regions, Timer

from .chrom_poly_stats import get_connect_torch_smat, get_connect_smat, connect_autocorr, connect_diag_autocorr


def gauss_weighted_contact(sep_mat, sigma=.020, radius_arr=None):
    if radius_arr is not None:
        surface_mat = radius_arr[np.newaxis, :] + radius_arr[:, np.newaxis]
        return np.exp(-np.power(sep_mat - surface_mat[:, :, np.newaxis], 2) /
                      (2. * (sigma * sigma)))
    return np.exp(-np.power(sep_mat, 2) / (2. * (sigma * sigma)))


def log_gauss_weighted_contact(sep_mat, sigma=.020):
    return -np.power(sep_mat, 2) / (2. * (sigma * sigma)) / np.log(10)


def get_link_energy_arrays(h5_data, write=False):
    """ Get the mean, standard deviation, and expected energy of all links in
    a bead-spring chain

    @param h5_data HDF5 data file to analyze with all raw data about filaments
    @param write If true, will write data directly to the analysis group in
                 the h5_data file.
    @return: TODO

    """
    sy_dat = h5_data['raw_data']['sylinders'][...]
    params = yaml.safe_load(h5_data.attrs['RunConfig'])
    k_spring = params['linkKappa']
    kbt = params['KBT']

    rest_length = params['linkGap'] + sy_dat[1:, 1, :] + sy_dat[:-1, 1, :]
    sep_vec = sy_dat[1:, 2:5, :] - sy_dat[:-1, 5:8, :]

    sep_mag = np.linalg.norm(sep_vec, axis=1)

    energy_arr = .5 * k_spring * np.power(sep_mag - rest_length, 2)
    mean_energy = np.mean(energy_arr, axis=0)
    sem_energy = stats.sem(energy_arr, axis=0)
    expt_energy = kbt * \
        (.5 - 1. /
         (1. + (k_spring * rest_length[0, 0] * rest_length[0, 0] / kbt)))
    if write:
        energy_dset = h5_data['analysis'].create_dataset(
            'link_energy', data=np.stack(mean_energy, sem_energy))
        energy_dset.attrs['nsylinders'] = energy_arr.shape[0]
    return mean_energy, sem_energy, kbt, expt_energy


def get_link_tension(h5_data, write=False):
    """ Get the force on a bead for every time step

    @param h5_data HDF5 data file to analyze with all raw data about filaments
    @param write If true, will write data directly to the analysis group in
                 the h5_data file.
    @return: TODO

    """
    sy_dat = h5_data['raw_data']['sylinders'][...]
    params = yaml.safe_load(h5_data.attrs['RunConfig'])
    k_spring = params['linkKappa']

    rest_length = params['linkGap'] + sy_dat[1:, 1, :] + sy_dat[:-1, 1, :]
    sep_vec = sy_dat[1:, 2:5, :] - sy_dat[:-1, 5:8, :]

    sep_mag = np.linalg.norm(sep_vec, axis=1)

    tension_arr = k_spring * (sep_mag - rest_length)
    return tension_arr


def get_contact_kymo_data(contact_mat):
    """Using a contact matrix, return a matrix with rows for the total contact
    probability of each bead and columns for each time point in simulation.

    @param contact_mat nbead x nbead x time points matrix of contact probabilities
    @return: TODO

    """
    # Remove interaction with self
    # contact_kymo =
    return (np.sum(contact_mat, axis=0) - 1)


def get_pos_kymo_data(h5_data, ts_range=(0, None), bead_range=(0, None), bins=100,
                      analysis=None):
    """Using center of all spheres, return a matrix with rows for n beads in a
    range along the axis of the two stationary end beads and columns for each
    time point in simulation.


    @param h5_data Simulation hdf5 data
    @return: TODO

    """
    # Get size of the system
    params = yaml.safe_load(h5_data.attrs['RunConfig'])
    sim_box_low = np.asarray(params['simBoxLow'])
    sim_box_high = np.asarray(params['simBoxHigh'])
    # Get center of mass of all beads for all times
    sy_dat = h5_data['raw_data']['sylinders'][
        bead_range[0]:bead_range[1], :, ts_range[0]:ts_range[-1]]
    com_arr = .5 * (sy_dat[:, 2:5, :] + sy_dat[:, 5:8, :])
    # Project bead positions onto unit vector from first to last bead
    proj_vec = com_arr[-1, :, 0] - com_arr[0, :, 0]
    proj_vec /= np.linalg.norm(proj_vec)
    proj_arr = np.einsum('ijk,j->ik', com_arr, proj_vec)
    # Set range of histograms
    range_min = np.dot(sim_box_low, proj_vec)
    range_max = np.dot(sim_box_high, proj_vec)
    # Make a series of histograms for each time point
    hist_arr = []
    for i, proj in enumerate(proj_arr.T):
        hist, bin_edges = np.histogram(proj, bins=bins,
                                       range=(range_min, range_max))
        hist_arr += [hist]

    hist_arr = np.asarray(hist_arr).T

    time_arr = h5_data['time'][ts_range[0]:ts_range[-1]]
    if analysis is not None:
        pos_kymo_dset = analysis.create_dataset('pos_kymo', data=hist_arr)
        pos_kymo_bin_edges = analysis.create_dataset(
            'pos_kymo_bin_edges', data=bin_edges)
        # Metadata for analysis
        pos_kymo_dset.attrs['bins'] = bins
        pos_kymo_dset.attrs['range'] = (range_min, range_max)
        pos_kymo_dset.attrs['timestep_range'] = ts_range
        pos_kymo_dset.attrs['time_range'] = (time_arr[0], time_arr[-1])
        # pos_kymo_dset.attrs['bead_range'] =
        pos_kymo_bin_edges.attrs['bins'] = bins
        pos_kymo_bin_edges.attrs['range'] = (range_min, range_max)
        pos_kymo_bin_edges.attrs['timestep_range'] = ts_range
        pos_kymo_bin_edges.attrs['time_range'] = (time_arr[0], time_arr[-1])
        # pos_kymo_bin_edges.attrs['bead_range'] = bead_range
    return time_arr, hist_arr, bin_edges


def get_contact_cond_data(time_arr, contact_kymo, threshold,
                          bead_win=0, time_win=0, analysis=None):
    """Given a contact kymo graph, finds condensates by regions that are above
    a certain contact threshold. This is done for every time point.

    @param time_arr TODO
    @param contact_kymo TODO
    @param threshold TODO
    @param bead_win TODO
    @param time_win TODO
    @return: TODO

    """
    # Doesn't matter which smoothing occurs first
    smooth_contact_kymo = smooth_kymo_mat(contact_kymo, bead_win, time_win)
    cond_edge_coords = []
    cond_num_arr = []
    for i, t in enumerate(time_arr):
        edges_inds = contiguous_regions(smooth_contact_kymo[:, i] > threshold)
        cond_num_arr += [len(edges_inds)]
        for start, end in edges_inds:
            cond_edge_coords += [[t, start, end]]
        # if len(edge_inds) == 0:
            # cond_edge_coords += [[t, 0, 0]]

    cond_edge_coords = np.asarray(cond_edge_coords)
    cond_num_arr = np.asarray(cond_num_arr)
    if analysis is not None:
        cond_edges_dset = analysis.create_dataset('contact_cond_edges',
                                                  data=cond_edge_coords)
        cond_num_dset = analysis.create_dataset('contact_cond_num',
                                                data=cond_num_arr)
        # Metadata for analysis
        cond_edges_dset.attrs['threshold'] = threshold
        cond_edges_dset.attrs['bead_win'] = bead_win
        cond_edges_dset.attrs['times_win'] = time_win
        cond_num_dset.attrs['threshold'] = threshold
        cond_num_dset.attrs['bead_win'] = bead_win
        cond_num_dset.attrs['times_win'] = time_win
    return cond_edge_coords, cond_num_arr


def smooth_kymo_mat(mat, y_win=0, time_win=0):
    """Smooth out a contact kymograph using a Savitzky–Golay filter

    @param mat Matrix that you want to smooth
    @param y_win Smoothing window in the bead index dimension
    @param time_win Smoothing window in the time dimension
    @return: TODO

    """
    smooth_kymo = deepcopy(mat)
    if y_win > 0:
        smooth_kymo = savgol_filter(mat, y_win, 3, axis=0)
    if time_win > 0:
        smooth_kymo = savgol_filter(smooth_kymo, time_win, 3, axis=-1)
    return smooth_kymo


def get_pos_cond_data(time_arr, pos_kymo, bin_centers, threshold,
                      bin_win=0, time_win=0, analysis=None):
    """TODO: Docstring for get_contact_cond_data.

    @param time_arr TODO
    @param contact_kymo TODO
    @param threshold TODO
    @param bead_win TODO
    @param time_win TODO
    @return: TODO

    """
    smooth_pos_kymo = smooth_kymo_mat(pos_kymo, bin_win, time_win)
    # Doesn't matter which smoothing occurs first
    cond_edge_coords = []
    cond_num_arr = []
    for i, t in enumerate(time_arr):
        edges_inds = contiguous_regions(smooth_pos_kymo[:, i] > threshold)
        cond_num_arr += [len(edges_inds)]
        for start, end in edges_inds:
            cond_edge_coords += [[t, bin_centers[start], bin_centers[end]]]

    cond_edge_coords = np.asarray(cond_edge_coords)
    cond_num_arr = np.asarray(cond_num_arr)
    if analysis is not None:
        pos_cond_edge_dset = analysis.create_dataset(
            'pos_cond_edges', data=cond_edge_coords)
        pos_cond_num_dset = analysis.create_dataset(
            'pos_cond_num', data=cond_num_arr)
        pos_cond_edge_dset.attrs['time_range'] = (time_arr[0], time_arr[-1])
        pos_cond_edge_dset.attrs['threshold'] = threshold
        pos_cond_edge_dset.attrs['bin_win'] = bin_win
        pos_cond_edge_dset.attrs['time_win'] = time_win
        pos_cond_num_dset.attrs['time_range'] = (time_arr[0], time_arr[-1])
        pos_cond_num_dset.attrs['threshold'] = threshold
        pos_cond_num_dset.attrs['bin_win'] = bin_win
        pos_cond_num_dset.attrs['time_win'] = time_win
    return cond_edge_coords, cond_num_arr


def get_sep_hist(h5_data, nbins=100, ss_ind=0, write=False):
    """Returns a 2D histogram of bead separations vs time

    @param h5_data TODO
    @return: TODO

    """
    params = yaml.safe_load(h5_data.attrs['RunConfig'])
    hist_min = params['sylinderDiameter'] * .8
    hist_max = params['sylinderDiameter'] * 1.2

    dist_hist = []
    dist_mat = get_sep_dist_mat(h5_data, ss_ind)

    for i in range(dist_mat.shape[-1]):
        hist, bin_edges = np.histogram(
            dist_mat[:, :, i].flatten(), nbins, range=(hist_min, hist_max))
        dist_hist += [hist * .5]

    return dist_hist, bin_edges


def get_sep_dist_mat(h5_data, ss_ind=0, bead_range=None, write=False):
    """Returns a NxNxM matrix of NXN filaments distances over M time points
    starting at ss_ind time point.

    @param h5_data TODO
    @return: TODO

    """
    sy_dat = h5_data['raw_data']['sylinders'][...]

    com_arr = .5 * (sy_dat[:, 2:5, :] + sy_dat[:, 5:8, :])
    if bead_range is not None:
        com_arr = com_arr[bead_range[0]:bead_range[1]]

    dist_mat = np.linalg.norm((com_arr[:, np.newaxis, :, ss_ind:] -
                               com_arr[np.newaxis, :, :, ss_ind:]),
                              axis=2)

    return dist_mat


def get_overlap_arrs(dist_mat, sy_diam):
    """Returns a NxNxM matrix of NXN filaments distances over M time points
    starting at ss_ind time point.

    @param h5_data TODO
    @return: TODO

    """
    is_overlap_mat = (dist_mat < sy_diam).astype(int)
    num_overlap = .5 * (is_overlap_mat.sum(axis=(0, 1))
                        - dist_mat.shape[0])  # remove self-overlap
    overlap_dist_mat = np.einsum('ijk, ijk -> ijk', dist_mat, is_overlap_mat)
    avg_overlap_arr = (.5 * overlap_dist_mat.sum(axis=(0, 1))) / num_overlap
    min_overlap_arr = np.ma.masked_values(overlap_dist_mat, 0).min(axis=(0, 1))

    return num_overlap, avg_overlap_arr, min_overlap_arr


def autocorr_bead_pos(com_arr, ignore_id=None):
    """Find the autocorrelation function for bead positions

    @param com_arr TODO
    @param ignore_id TODO
    @return: TODO

    """

    #com_rel_arr = com_arr - com_arr.mean(axis=-1)[:, :, np.newaxis]
    com_rel_arr = com_arr[...]

    if ignore_id is not None:
        com_rel_arr = np.delete(com_rel_arr, ignore_id, axis=0)
    nsteps = com_rel_arr.shape[-1]
    nbeads = com_rel_arr.shape[0]
    auto_corr = np.zeros((nbeads, nsteps))
    for t in range(nsteps):
        for j in range(nsteps - t):
            auto_corr[:, t] += np.einsum('ij,ij->i', com_rel_arr[:, :, t + j],
                                         com_rel_arr[:, :, j])
        auto_corr[:, t] /= (nsteps - t)

    return auto_corr


def distr_hists(pos_mat, free_frac_chain=.5,
                rel_ind=0, nbins=100, hist_max=1.):
    """TODO: Docstring for radial_distr.
    @param pos_mat TODO
    @param free_frac_chain TODO
    @param rel_ind TODO
    @param nbins TODO
    @return: TODO
    """
    nbeads = pos_mat.shape[0]
    ind = int(nbeads * free_frac_chain)

    rel_vec_arr = pos_mat[ind, :, :] - pos_mat[rel_ind, :, :]
    dist_arr = np.linalg.norm(rel_vec_arr, axis=0)

    dist_hist, dist_bin_edges = np.histogram(
        dist_arr, nbins, range=[0, hist_max], density=True)
    z_rho_hist, rho_bin_edges, z_bin_edges = np.histogram2d(
        np.linalg.norm(rel_vec_arr[:-1, :], axis=0), rel_vec_arr[-1, :],
        int(nbins / 2), range=[[0, hist_max], [-hist_max, hist_max]], density=True)

    return ((dist_hist, dist_bin_edges),
            (z_rho_hist, rho_bin_edges, z_bin_edges))


def total_distr_hists(pos_mat, rel_ind=0, nbins=100, hist_max=1):
    """TODO: Docstring for radial_distr.
    @param pos_mat TODO
    @param free_frac_chain TODO
    @param rel_ind TODO
    @param nbins TODO
    @return: TODO
    """
    rel_vec_arr = pos_mat - (pos_mat[rel_ind])[np.newaxis, :, :]
    dist_arr = np.linalg.norm(rel_vec_arr, axis=1).flatten()

    dist_hist, dist_bin_edges = np.histogram(
        dist_arr, nbins, range=[0, hist_max], density=True)
    z_rho_hist, rho_bin_edges, z_bin_edges = np.histogram2d(
        np.linalg.norm(rel_vec_arr[:, :-1, :], axis=1).flatten(),
        rel_vec_arr[:, -1, :].flatten(), int(nbins / 2),
        range=[[0, hist_max], [-hist_max, hist_max]], density=True)

    return ((dist_hist, dist_bin_edges),
            (z_rho_hist, rho_bin_edges, z_bin_edges))


def cart_distr_hists(pos_mat, rel_pos, e0_ind, e1_ind, nbins=100, hist_max=1.):
    """TODO: Docstring for radial_distr.
    @param pos_mat TODO
    @param rel_ind TODO
    @param nbins TODO
    @return: TODO
    """
    rel_vec_arr = pos_mat - (rel_pos)[np.newaxis, :, :]
    e0_e1_hist, e0_edges, e1_edges = np.histogram2d(
        rel_vec_arr[:, e0_ind, :].flatten(),
        rel_vec_arr[:, e1_ind, :].flatten(),
        int(nbins / 2),
        range=[[-hist_max, hist_max], [-hist_max, hist_max]], density=True)

    return (e0_e1_hist, e0_edges, e1_edges)


def cylin_distr_hists(pos_mat, zero_pos, z_uvec, nbins=100, hist_max=1.):
    """TODO: Docstring for cylindrical histogram.
    @param pos_mat TODO
    @param rel_ind TODO
    @param nbins TODO
    @return: TODO
    """
    rel_vec_arr = pos_mat - (zero_pos)[np.newaxis, :, :]
    z_proj_arr = np.einsum('ijk,jk->ik', rel_vec_arr, z_uvec)
    rho_proj_arr = np.linalg.norm(
        rel_vec_arr - np.einsum('jk,ik->ijk', z_uvec, z_proj_arr), axis=1)
    rho_z_hist, rho_bin_edges, z_bin_edges = np.histogram2d(
        rho_proj_arr.flatten(), z_proj_arr.flatten(), int(nbins / 2),
        range=[[0, hist_max], [-hist_max, hist_max]], density=True)

    return (rho_z_hist, rho_bin_edges, z_bin_edges)


def rad_distr_hists(pos_mat, zero_pos, nbins=100, hist_max=1.):
    """TODO: Docstring for cylindrical histogram.
    @param pos_mat TODO
    @param nbins TODO
    @return: TODO
    """
    rel_vec_arr = pos_mat - (zero_pos)[np.newaxis, :, :]
    rad_arr = np.linalg.norm(rel_vec_arr, axis=1).flatten()

    rad_hist, rad_bin_edges = np.histogram(
        rad_arr, nbins, range=[
            0, hist_max], density=True)
    return (rad_hist, rad_bin_edges)


def rad_distr_func_at_t(dist_mat, nbins=100, hist_max=1., orig_density=1):
    """ Get the radial distribution function for a selection of beads
    @param pos_mat TODO
    @param nbins TODO
    @return: TODO
    """

    rad_distr_func, rad_bin_edges = np.histogram(
        dist_mat.flatten(), nbins, range=[
            0, hist_max], density=False)
    dr = rad_bin_edges[1:] - rad_bin_edges[:-1]
    rad = .5 * (rad_bin_edges[1:] + rad_bin_edges[:-1])
    rad_distr_func = np.divide(
        rad_distr_func, np.pi * np.power(rad, 2.) * dr * orig_density * dist_mat.size)
    return (rad_distr_func, rad_bin_edges)


def get_all_rog_stats(pos_mat, rel_ind=0):
    rel_vec_arr = pos_mat - (pos_mat[rel_ind])[np.newaxis, :, :]
    pos_avg_arr = rel_vec_arr.mean(axis=2)
    pos_std_arr = rel_vec_arr.std(axis=2)
    #pos_mean_sqr_arr = np.mean(np.einsum('ijk,ijk->ik',rel_vec_arr, rel_vec_arr), axis=1)
    rad_pos_arr = np.linalg.norm(rel_vec_arr, axis=1)
    rad_mean_arr = np.power(rad_pos_arr, 2).mean(axis=1)
    rad_std_arr = rad_pos_arr.std(axis=1)

    return(pos_avg_arr, pos_std_arr, rad_mean_arr, rad_std_arr)


def get_contact_mat_analysis(com_arr, sigma=.02, avg_block_step=1, log=True,
                             radius_arr=None, analysis=None):
    """Generate (and store if given an HDF5 directory) all analysis related to
    contact matrices related to chromatin. This is includes separation matrix at
    every time point (this is not stored because of the size), average contact
    matrix, and contact kymograph.

    Parameters
    ----------
    com_arr : NxDxT ndarray
        Matrix of particles centers of masses
    sigma : float, optional
        _description_, by default .02
    avg_block_step : int, optional
        _description_, by default 1
    log : bool, optional
        _description_, by default True
    radius_arr : _type_, optional
        _description_, by default None
    analysis : _type_, optional
        _description_, by default None

    Returns
    -------
    _type_
        _description_
    """
    reduc_com_arr = com_arr[::avg_block_step, :, :]  # simple downsampling

    sep_mat = np.linalg.norm(
        reduc_com_arr[:, np.newaxis, :, :] - reduc_com_arr[np.newaxis, :, :, :], axis=2)
    # log_contact_mat = log_gauss_weighted_contact(sep_mat, sigma)
    contact_mat = gauss_weighted_contact(sep_mat, sigma, radius_arr)
    contact_kymo = get_contact_kymo_data(contact_mat)

    if log:
        avg_contact_mat = np.log(contact_mat.mean(axis=-1))
    else:
        avg_contact_mat = contact_mat.mean(axis=-1)

    if analysis is not None:
        avg_contact_mat_dset = analysis.create_dataset('avg_contact_mat',
                                                       data=avg_contact_mat)
        avg_contact_mat_dset.attrs['sigma'] = sigma
        avg_contact_mat_dset.attrs['avg_block_step'] = avg_block_step
        avg_contact_mat_dset.attrs['log'] = log

        contact_kymo = analysis.create_dataset('contact_kymo',
                                               data=contact_kymo)
        contact_kymo.attrs['sigma'] = sigma
        contact_kymo.attrs['avg_block_step'] = avg_block_step
        # contact_kymo.attrs['log'] = log
        if radius_arr is not None:
            avg_contact_mat_dset.attrs['radius_arr'] = radius_arr
            contact_kymo.attrs['radius_arr'] = radius_arr
    return avg_contact_mat, contact_mat, contact_kymo


def get_end_end_distance(com_arr):
    return np.linalg.norm(com_arr[0, :, :] - com_arr[-1, :, :], axis=0)


def calc_rad_of_gyration(com_arr, device='cpu'):
    """Calculate the radius of gyration of filament

    @param com_arr TODO
    @return: TODO

    """

    tcom_arr = torch.from_numpy(com_arr).to(device)
    rel_pos_arr = tcom_arr - tcom_arr.mean(dim=0)

    n_beads = float(rel_pos_arr.size(0))

    rog_sqr_arr = torch.einsum(
        'ijk,ijk->k', rel_pos_arr, rel_pos_arr) / n_beads
    return torch.sqrt(rog_sqr_arr)


def find_neighbors(com_arr, diam, time_ind=0):
    """Find beads that are in close proximity with one another at any given time.

    """
    neighbor_mat = (np.linalg.norm((com_arr[:, np.newaxis, :, time_ind] -
                                    com_arr[np.newaxis, :, :, time_ind]),
                                   axis=2) < diam * 1.2).astype(int)
    return neighbor_mat


def create_connect_hdf5(h5_raw_path, force=False, verbose=False,
                        start_ind=0,
                        end_ind=None):
    # Create path for cluster data file
    connect_path = (h5_raw_path.parent /
                    f'connect_diag_analysis.h5')
    if connect_path.exists():
        if not force:
            print(
                f"Warning: connect data file {connect_path.name} exists and was not overwritten.")
            return
        connect_path.unlink()

    # Run analysis
    with h5py.File(h5_raw_path, 'r') as h5_data:
        time_arr = h5_data['time'][start_ind:end_ind]
        lag_time_arr = time_arr - time_arr[0]
        prot_dat = h5_data['raw_data/proteins'][:, :, start_ind:end_ind]
        bead_num = h5_data['raw_data/sylinders'][...].shape[0]
        connect_mat_list = []
        # timer = Timer()
        for i in range(time_arr.size):
            connect_mat_list += [get_connect_torch_smat(
                prot_dat[:, :, i], bead_num)]
        # timer.log()

        # timer.milestone()
        n = len(connect_mat_list)
        avg_connect_mat = reduce(lambda x, y: x + y, connect_mat_list)
        avg_connect_mat = avg_connect_mat.to_dense().numpy() / n
        # timer.log()

    with h5py.File(connect_path, 'w') as h5_cnct:
        _ = h5_cnct.create_dataset('time', data=time_arr)
        _ = h5_cnct.create_dataset('lag_time', data=lag_time_arr)
        _ = h5_cnct.create_dataset('avg_connect_mat', data=avg_connect_mat)

        # timer.milestone()
        ac_arr = connect_diag_autocorr(connect_mat_list[:])
        _ = h5_cnct.create_dataset('autocorr', data=ac_arr)
        # timer.log()


##########################################
if __name__ == "__main__":
    print("Not implemented yet")
