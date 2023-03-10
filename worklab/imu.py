import copy
from warnings import warn

import numpy as np
from scipy.integrate import cumtrapz
from scipy.signal import periodogram, find_peaks

from .utils import lowpass_butter, pd_interp


def resample_imu(sessiondata, sfreq=400.):
    """
    Resample all devices and sensors to new sample frequency.

    Resamples all devices and sensors to new sample frequency. Sample intervals are not fixed with NGIMU's so resampling
    before further analysis is recommended. Translated from xio-Technologies [2]_.

    Parameters
    ----------
    sessiondata : dict
        original session data structure to be resampled
    sfreq : float
        new intended sample frequency

    Returns
    -------
    sessiondata : dict
        resampled session data structure

    References
    ----------
    .. [2] https://github.com/xioTechnologies/NGIMU-MATLAB-Import-Logged-Data-Example

    """
    end_time = 0
    for device in sessiondata:
        max_time = sessiondata[device]["time"].max()
        end_time = max_time if max_time > end_time else end_time

    new_time = np.arange(0, end_time, 1 / sfreq)

    for device in sessiondata:
        if device == "quaternion":
            sessiondata[device] = pd_interp(sessiondata[device], "time", new_time)
            sessiondata[device] *= (1 / np.linalg.norm(sessiondata[device], axis=0))
        elif device == "matrix":
            warn("Rotation matrix cannot be resampled. This dataframe has been removed")
        else:
            sessiondata[device] = pd_interp(sessiondata[device], "time", new_time)
    return sessiondata


def process_imu(sessiondata, camber=18, wsize=0.32, wbase=0.80, n_sensors=3, sensor_type='ngimu', inplace=False):
    """
    Calculate wheelchair kinematic variables based on NGIMU data

    Parameters
    ----------
    sessiondata : dict
        original sessiondata structure
    camber : float
        camber angle in degrees
    wsize : float
        radius of the wheels
    wbase : float
        width of wheelbase
    n_sensors: float
        number of sensors used, 1: right wheel, 2: right wheel and frame, 3: right, left wheel and frame
    sensor_type: string
        type of sensor, 'ngimu' is for xio-technologies, 'move' is for movesense
    inplace : bool
        performs operation inplace


    Returns
    -------
    sessiondata : dict
        sessiondata structure with processed data

    """
    if not inplace:
        sessiondata = copy.deepcopy(sessiondata)
    if n_sensors == 3:
        left = sessiondata["left"]
    frame = sessiondata["frame"]
    right = sessiondata["right"]

    sfreq = 1 / frame["time"].diff().mean()

    # Wheelchair camber correction
    deg2rad = np.pi / 180
    right["gyro_cor"] = right["gyroscope_y"] + np.tan(camber * deg2rad) * (
            frame["gyroscope_z"] * np.cos(camber * deg2rad))

    if n_sensors == 3:
        left["gyro_cor"] = left["gyroscope_y"] - np.tan(camber * deg2rad) * (
                            frame["gyroscope_z"] * np.cos(camber * deg2rad))
        frame["gyro_cor"] = (right["gyro_cor"] + left["gyro_cor"]) / 2
    else:
        frame["gyro_cor"] = right["gyro_cor"]

    # Calculation of rotations, rotational velocity and acceleration
    frame["rot_vel"] = lowpass_butter(frame["gyroscope_z"], sfreq=sfreq, cutoff=10)
    frame["rot"] = cumtrapz(abs(frame["rot_vel"]) / sfreq, initial=0.0)
    frame["rot_acc"] = np.gradient(frame["rot_vel"]) * sfreq

    # Calculation of speed, acceleration and distance
    right["vel"] = right["gyro_cor"] * wsize * deg2rad  # angular velocity to linear velocity
    right["dist"] = cumtrapz(right["vel"] / sfreq, initial=0.0)  # integral of velocity gives distance

    if n_sensors == 3:
        left["vel"] = left["gyro_cor"] * wsize * deg2rad
        left["dist"] = cumtrapz(left["vel"] / sfreq, initial=0.0)
        frame["vel"] = (right["vel"] + left["vel"]) / 2  # mean velocity both sides
        frame["dist"] = (right["dist"] + left["dist"]) / 2  # mean distance
    else:
        frame["vel"] = right["vel"]
        frame["dist"] = right["dist"]

    frame["vel"] = lowpass_butter(frame["vel"], sfreq=sfreq, cutoff=10)
    frame["acc_wheel"] = np.gradient(frame["vel"]) * sfreq  # mean acceleration from velocity

    if sensor_type == 'ngimu':  # Acceleration for NGIMU is in g
        frame["accelerometer_x"] = frame["accelerometer_x"] * 9.81
    frame['acc'] = lowpass_butter(frame['accelerometer_x'], sfreq=sfreq, cutoff=20)
    # distance in the x and y direction
    frame["dist_y"] = cumtrapz(
        np.gradient(frame["dist"]) * np.sin(np.deg2rad(cumtrapz(frame["rot_vel"] / sfreq, initial=0.0))),
        initial=0.0)
    frame["dist_x"] = cumtrapz(
        np.gradient(frame["dist"]) * np.cos(np.deg2rad(cumtrapz(frame["rot_vel"] / sfreq, initial=0.0))),
        initial=0.0)

    """Perform skid correction from Rienk vd Slikke, please refer and reference to: Van der Slikke, R. M. A., et. al. 
    Wheel skid correction is a prerequisite to reliably measure wheelchair sports kinematics based on inertial sensors. 
    Procedia Engineering, 112, 207-212."""
    frame["skid_vel_right"] = right["vel"]  # Calculate frame centre distance
    frame["skid_vel_right"] -= np.tan(np.deg2rad(frame["gyroscope_z"] / sfreq)) * wbase / 2 * sfreq

    if n_sensors == 3:
        frame["skid_vel_left"] = left["vel"]
        frame["skid_vel_left"] += np.tan(np.deg2rad(frame["gyroscope_z"] / sfreq)) * wbase / 2 * sfreq

        r_ratio0 = np.abs(right["vel"]) / (np.abs(right["vel"]) + np.abs(left["vel"]))  # Ratio left and right
        l_ratio0 = np.abs(left["vel"]) / (np.abs(right["vel"]) + np.abs(left["vel"]))
        r_ratio1 = np.abs(np.gradient(left["vel"])) / (np.abs(np.gradient(right["vel"]))
                                                       + np.abs(np.gradient(left["vel"])))
        l_ratio1 = np.abs(np.gradient(right["vel"])) / (np.abs(np.gradient(right["vel"]))
                                                        + np.abs(np.gradient(left["vel"])))

        comb_ratio = (r_ratio0 * r_ratio1) / ((r_ratio0 * r_ratio1) + (l_ratio0 * l_ratio1))  # Combine speed ratios
        comb_ratio.fillna(value=0., inplace=True)
        comb_ratio = lowpass_butter(comb_ratio, sfreq=sfreq, cutoff=20)  # Filter the signal
        comb_ratio = np.clip(comb_ratio, 0, 1)  # clamp Combratio values, not in df
        frame["skid_vel"] = (frame["skid_vel_right"] * comb_ratio) + (frame["skid_vel_left"] * (1 - comb_ratio))
    else:
        frame["skid_vel"] = frame["skid_vel_right"]

    # frame["skid_vel"] = lowpass_butter(frame["skid_vel"], sfreq=sfreq, cutoff=10)
    frame["skid_dist"] = cumtrapz(frame["skid_vel"], initial=0.0) / sfreq  # Combined skid distance
    return sessiondata


def change_imu_orientation(sessiondata, inplace=False):
    """
    Changes IMU orientation from in-wheel to on-wheel

    Parameters
    ----------
    sessiondata : dict
        original sessiondata structure
    inplace : bool
        perform operation inplace

    Returns
    -------
    sessiondata : dict
        sessiondata with reoriented gyroscope data

    """
    if not inplace:
        sessiondata = copy.deepcopy(sessiondata)

    order = {"gyroscope_x": "gyroscope_z", "gyroscope_z": "gyroscope_y", "gyroscope_y": "gyroscope_x"}
    sessiondata["left"]["sensors"].rename(columns=order, inplace=True)
    sessiondata["right"]["sensors"].rename(columns=order, inplace=True)
    sessiondata["right"]["sensors"]["gyroscope_y"] *= -1
    return sessiondata


def push_imu(acceleration, sfreq=400.):
    """
    Push detection based on velocity signal of IMU on a wheelchair [3]_.

    Parameters
    ----------
    acceleration : np.array, pd.Series
        acceleration data structure
    sfreq : float
        sampling frequency
    Returns
    -------
        push_idx, acc_filt, n_pushes, cycle_time, push_freq

    References
    ----------
    .. [3] van der Slikke, R., Berger, M., Bregman, D., & Veeger, D. (2016). Push characteristics in wheelchair
    court sport sprinting. Procedia engineering, 147, 730-734.

    """
    min_freq = 1.2
    f, pxx = periodogram(acceleration - np.mean(acceleration), sfreq)
    min_freq_f = len(f[f < min_freq])
    max_freq_ind_temp = np.argmax(pxx[min_freq_f:min_freq_f * 5])
    max_freq = f[min_freq_f + max_freq_ind_temp]
    max_freq = min(max_freq, 3.)
    cutoff_freq = 1.5 * max_freq
    acc_filt = lowpass_butter(acceleration, sfreq=sfreq, cutoff=cutoff_freq)
    std_acc = np.std(acc_filt)
    push_idx, peak_char = find_peaks(acc_filt, height=std_acc / 2,
                                     distance=round(1 / (max_freq * 1.5) * sfreq), prominence=std_acc / 2)
    n_pushes = len(push_idx)
    push_freq = n_pushes / (len(acceleration) / sfreq)
    cycle_time = list()

    for n in range(0, len(push_idx) - 1):
        cycle_time.append((push_idx[n + 1] / sfreq) - (push_idx[n] / sfreq))

    return push_idx, acc_filt, n_pushes, cycle_time, push_freq
