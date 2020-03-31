import csv
import re
from collections import defaultdict
from glob import glob
from os import listdir, path
from struct import unpack

import numpy as np
import pandas as pd

from .utils import pick_file, pd_dt_to_s, merge_chars


def load(filename=""):
    """
    Attempt to load a common data format.

    Most important function in the module. Provides high level loading function to load common data formats.
    If no filename is given will try to load filename using a file dialog. Will try to infer data source from filename.
    Try to use a specific load function if load cannot infer the datatype.

    Parameters
    ----------
    filename : str
        name or path to file of interest

    Returns
    -------
    data : pd.DataFrame
        raw data, format depends on source, but is usually a dict or pandas DataFrame

    See Also
    --------
    load_bike, load_esseda, load_hsb, load_n3d, load_opti, load_optitrack, load_session, load_spiro, load_spline,
    load_sw

    """
    filename = pick_file().lower() if not filename else filename.lower()
    if not filename:
        raise Exception("Please provide a filename")
    print("\n" + "=" * 80)
    print(f"Initializing loading for {filename} ...")
    if ".xlsx" in filename or "spiro" in filename:  # COSMED
        print("File identified as COSMED datafile. Attempting to load ...")
        data = load_spiro(filename)
    elif ".xls" in filename and "fiets" not in filename:
        print("File identified as Esseda datafile. Attempting to load ...")
        data = load_esseda(filename)
    elif "HSB.csv" in filename:
        print("File identified as HSB-logger datafile. Attempting to load ...")
        data = load_hsb(filename)
    elif ".txt" in filename and "drag" not in filename:
        print("File identified as SMARTwheel datafile. Attempting to load ...")
        data = load_sw(filename)
    elif ".dat" in filename:
        print("File identified as Optipush datafile. Attempting to load ...")
        data = load_opti(filename)
    elif "fiets" in filename:
        print("File identified as Bicycle ergometer datafile. Attempting to load ...")
        data = load_bike(filename)
    elif ".n3d" in filename:
        print("File identified as Optotrak datafile. Attempting to load ...")
        data, _ = load_n3d(filename)
    elif ".xml" in filename:
        print("Folder identified as NGIMU folder. Attempting to load ...")
        data = load_session(path.split(filename)[0], filenames=["sensors"])
    elif "drag" in filename:
        print("File identified as dragtest datafile. Attempting to load ...")
        data = load_drag_test(filename)
    elif ".csv" in filename:
        print("File identified as optitrack datafile. Attempting to load ...")
        data = load_optitrack(filename)
    else:
        raise Exception("No file name given or could not identify data source with load!!")
    print("Data loaded!")
    print("=" * 80 + "\n")
    return data


def load_spiro(filename):
    """
    Loads COSMED spirometer data from Excel file.

    Loads spirometer data to a pandas DataFrame, converts time to seconds (not datetime), computes power from the energy
    expenditure, computes weights from the time difference between samples, if no heart rate data is available it fills
    the column with np.NaNs, also includes VO2 and VCO2. Returns a DataFrame with:

    +------------+----------------------+-----------+
    | Column     | Data                 | Unit      |
    +============+======================+===========+
    | time       | time at breath       | s         |
    +------------+----------------------+-----------+
    | power      | power at breath      | N         |
    +------------+----------------------+-----------+
    | weights    | sample weight        |           |
    +------------+----------------------+-----------+
    | HR         | heart rate           | bpm       |
    +------------+----------------------+-----------+
    | VO2        | torque around x-axis | [mL/min]  |
    +------------+----------------------+-----------+
    | VCO2       | torque around y-axis | [mL/min]  |
    +------------+----------------------+-----------+

    Parameters
    ----------
    filename : str
        full file path or file in existing path from COSMED spirometer

    Returns
    -------
    data : pd.DataFrame
        Spirometer data in pandas DataFrame

    """
    data = pd.read_excel(filename, skiprows=[1, 2], usecols="J:XX")
    data["time"] = data.apply(lambda row: pd_dt_to_s(row["t"]), axis=1)  # hh:mm:ss to s
    data["power"] = data["EEm"] * 4184 / 60  # added power (kcal/min to J/s)
    data["weights"] = np.insert(np.diff(data["time"]), 0, 0)  # used for calculating weighted average
    data["HR"] = np.NaN if "HR" not in data else data["HR"]  # missing when sensor is not detected
    data = data[data["time"] > 0]  # remove "missing" data
    return data[["time", "HR", "power", "VO2", "VCO2", "weights"]]


def load_opti(filename):
    """
    Loads Optipush data from .data file.

    Loads Optipush data to a pandas DataFrame, converts angle to radians, and flips torque (Tz). Returns a DataFrame
    with:

    +------------+----------------------+-----------+
    | Column     | Data                 | Unit      |
    +============+======================+===========+
    | time       | sample time          | s         |
    +------------+----------------------+-----------+
    | fx         | force on local x-axis| N         |
    +------------+----------------------+-----------+
    | fy         | force on local y-axis| N         |
    +------------+----------------------+-----------+
    | fz         | force on local z-axis| N         |
    +------------+----------------------+-----------+
    | mx         | torque around x-axis | Nm        |
    +------------+----------------------+-----------+
    | my         | torque around y-axis | Nm        |
    +------------+----------------------+-----------+
    | torque     | torque around z-axis | Nm        |
    +------------+----------------------+-----------+
    | angle      | unwrapped wheel angle| rad       |
    +------------+----------------------+-----------+

    .. note:: Optipush uses a local coordinate system

    Parameters
    ----------
    filename : str
        filename or path to Optipush .data (.csv) file

    Returns
    -------
    opti_df : pd.DataFrame
        Raw Optipush data in a pandas DataFrame

    See Also
    --------
    load_sw : Load measurement wheel data from a SMARTwheel

    """
    names = ["time", "fx", "fy", "fz", "mx", "my", "torque", "angle"]
    dtypes = {name: np.float64 for name in names}
    usecols = [0, 3, 4, 5, 6, 7, 8, 9]
    opti_df = pd.read_csv(filename, names=names, delimiter="\t", usecols=usecols, dtype=dtypes, skiprows=12)
    opti_df["angle"] *= (np.pi / 180)
    opti_df["torque"] *= -1
    return opti_df


def load_sw(filename, sfreq=200):
    """
    Loads SMARTwheel data from .txt file.

    Loads SMARTwheel data to a pandas DataFrame, converts angle to radians and unwraps it. Returns a DataFrame with:

    +------------+-----------------------+-----------+
    | Column     | Data                  | Unit      |
    +============+=======================+===========+
    | time       | sample time           | s         |
    +------------+-----------------------+-----------+
    | fx         | force on global x-axis| N         |
    +------------+-----------------------+-----------+
    | fy         | force on global y-axis| N         |
    +------------+-----------------------+-----------+
    | fz         | force on global z-axis| N         |
    +------------+-----------------------+-----------+
    | mx         | torque around x-axis  | Nm        |
    +------------+-----------------------+-----------+
    | my         | torque around y-axis  | Nm        |
    +------------+-----------------------+-----------+
    | torque     | torque around z-axis  | Nm        |
    +------------+-----------------------+-----------+
    | angle      | unwrapped wheel angle | rad       |
    +------------+-----------------------+-----------+

    .. note:: SMARTwheel uses a global coordinate system

    Parameters
    ----------
    filename : str
        filename or path to SMARTwheel .data (.csv) file
    sfreq : int
        sample frequency of SMARTwheel, default is 200Hz

    Returns
    -------
    sw_df : pd.DataFrame
        Raw SMARTwheel data in a pandas DataFrame

    See Also
    --------
    load_opti : Load measurement wheel data from an Optipush wheel.

    """
    names = ["time", "angle", "fx", "fy", "fz", "mx", "my", "torque"]
    dtypes = {name: np.float64 for name in names}
    usecols = [1, 3, 18, 19, 20, 21, 22, 23]
    sw_df = pd.read_csv(filename, names=names, usecols=usecols, dtype=dtypes)
    sw_df["time"] /= sfreq
    sw_df["angle"] = np.unwrap(sw_df["angle"] * (np.pi / 180)) * - 1  # in radians
    return sw_df


def load_hsb(filename):
    """
    Loads HSB ergometer data from HSB datafile.

    Loads ergometer data measured with the HSBlogger2 and returns the data in a dictionary for the left and right module
    with a DataFrame each that contains time, force, and speed. HSB files are generally only for troubleshooting and
    testing that is beyond the scope of LEM.

    Parameters
    ----------
    filename : str
        full file path or file in existing path from HSB .csv file

    Returns
    -------
    data : dict
        dictionary with DataFrame for left and right module

    """
    # noinspection PyTypeChecker
    data = {"left": defaultdict(list), "right": defaultdict(list)}
    with open(filename, 'r') as csvfile:
        reader = csv.reader(csvfile, delimiter=';')
        next(reader, None)  # skip header
        for row in reader:
            if row[0] == "0":
                data["left"]["time"].append(float(row[1].replace(",", ".")))
                data["left"]["force"].append(float(row[2].replace(",", ".")))
                data["left"]["speed"].append(float(row[3].replace(",", ".")))
            else:
                data["right"]["time"].append(float(row[1].replace(",", ".")))
                data["right"]["force"].append(float(row[2].replace(",", ".")))
                data["right"]["speed"].append(float(row[3].replace(",", ".")))
    for side in data:
        data[side] = {dkey: np.asarray(data[side][dkey]) for dkey in data[side]}  # Convert to arrays
        if data[side]["time"].size:  # Remove time offset
            data[side]["time"] -= data[side]["time"][0]
            if data[side]["time"][1] - data[side]["time"][0] > 0.1:
                data[side]["time"] *= 0.01
        else:
            print("No right module detected!")
            data["right"] = {key: np.zeros(data["left"]["time"].size) for key in data["right"]}
    for side in data:
        if np.mean(data[side]["force"]) < 0:
            data[side]["force"] *= -1  # Flip force direction
        if np.mean(data[side]["speed"]) < 0:
            data[side]["speed"] *= -1  # Flip speed direction
    data = {"left": pd.DataFrame(data["left"]), "right": pd.DataFrame(data["right"])}
    return data


def load_esseda(filename):
    """
    Loads HSB ergometer data from LEM datafile.

    Loads ergometer data measured with LEM and returns the data in a dictionary for the left and right module with a
    DataFrame each that contains time, force (on wheel), and speed.

    Parameters
    ----------
    filename : str
        full file path or file in existing path from LEM Excel sheet (.xls)

    Returns
    -------
    data : dict
        dictionary with DataFrame for left and right module

    See Also
    --------
    load_wheelchair: Load wheelchair information from LEM datafile.
    load_spline: Load calibration splines from LEM datafile.

    """
    df = pd.read_excel(filename, sheet_name="HSB")
    df = df.dropna(axis=1, how='all')  # remove empty columns
    df = df.apply(lambda col: pd.to_numeric(col.str.replace(',', '.')) if isinstance(col[0], str) else col, axis=0)

    cols = len(df.columns) // 5  # LEM does this annoying thing where it starts in new columns
    mats = np.split(df.values, cols, axis=1)
    dmat = np.concatenate(tuple(mats), axis=0)

    data = {"left": pd.DataFrame(), "right": pd.DataFrame()}
    data["left"]["time"] = dmat[:, 0]
    data["left"]["force"] = dmat[:, 1]
    data["left"]["speed"] = dmat[:, 3]
    data["right"]["time"] = dmat[:, 0]
    data["right"]["force"] = dmat[:, 2]
    data["right"]["speed"] = dmat[:, 4]

    for side in data:
        data[side].dropna(inplace=True)
        data[side]["time"] -= data[side]["time"][0]  # time should start at 0.0s

    n_samples = min(len(data["left"]), len(data["right"]))  # ensure equal sized left and right dataframes
    data["left"] = data["left"].iloc[:n_samples, :]
    data["right"] = data["right"].iloc[:n_samples, :]
    return data


def load_wheelchair(filename):
    """
    Loads wheelchair from LEM datafile.

    Loads the wheelchair data from a LEM datafile. Note that LEM only recently added this to their exports. Returns:

    +------------+-----------------------+-----------+
    | Column     | Data                  | Unit      |
    +============+=======================+===========+
    | name       | chair name            |           |
    +------------+-----------------------+-----------+
    | rimsize    | radius of handrim     | m         |
    +------------+-----------------------+-----------+
    | wheelsize  | radius of the wheel   | m         |
    +------------+-----------------------+-----------+
    | weight     | weight of the chair   | kg        |
    +------------+-----------------------+-----------+

    Parameters
    ----------
    filename : str
        full file path or file in existing path from LEM Excel sheet (.xls)

    Returns
    -------
    wheelchair : dict
        dictionary with wheelchair information

    See Also
    --------
    load_esseda: Load HSB data from LEM datafile.
    load_spline: Load calibration splines from LEM datafile.

    """
    data = pd.read_excel(filename, sheet_name="Wheelchair Settings")
    wheelchair = {"name": data.iloc[1, 1],
                  "rimsize": float(data.iloc[7, 1]) / 1000 / 2,
                  "wheelsize": float(data.iloc[8, 1]) / 1000 / 2,
                  "wheelbase": float(data.iloc[9, 1]) / 1000,
                  "weight": float(data.iloc[10, 1])}
    return wheelchair


def load_bike(filename):
    """
    Load bicycle ergometer data from LEM datafile.

    Loads bicycle ergometer data from LEM to a pandas DataFrame containing time, load, rpm, and heart rate (HR).

    Parameters
    ----------
    filename : str
        full file path or file in existing path from LEM Excel sheet (.xls)

    Returns
    -------
    data : pd.DataFrame
        DataFrame with time, load, rpm, and HR data

    """
    return pd.read_excel(filename, sheet_name=2, names=["time", "load", "rpm", "HR"])  # 5 Hz data


def load_spline(filename):
    """
    Load wheelchair ergometer calibration spline from LEM datafile.

    Loads Esseda calibration spline from LEM which includes all forces (at the roller) at the different calibration
    points (1:10:1 km/h).

    Parameters
    ----------
    filename : object
        full file path or file in existing path from LEM excel file

    Returns
    -------
    data : dict
        left and right calibration values

    """
    gear_ratio = 4  # Roller to loadcell
    data = {"left": None, "right": None}
    df = pd.read_excel(filename, sheet_name="Devices", header=5, skiprows=0)
    df = df.iloc[:, [1, 2]]  # Remove random columns
    df = df[8:88:8]  # Remove random rows
    data["left"] = df.values[:, 0]
    data["right"] = df.values[:, 1]
    for side in data:
        for idx, value in enumerate(data[side]):
            data[side][idx] = value.replace(",", ".")
        data[side] = data[side].astype(float) * gear_ratio
    return data


def load_n3d(filename, verbose=True):
    """
    Reads NDI-Optotrak data files

    Parameters
    ----------
    filename : str
        Optotrak data file (.n3d)
    verbose : bool
        Print some information about the data from the file.
        If True (default) it prints the information.

    Returns
    -------
    optodata : ndarray
        Multidimensional numpy array with xyz [in mm], marker, and sample dimensions.

    """
    with open(filename, "rb") as f:
        content = f.read()

    # filetype = unpack('c', content[0:1])[0]
    items = unpack('h', content[1:3])[0]  # int16, number of markers
    subitems = unpack('h', content[3:5])[0]  # int16, number of dimensions (usually 3)
    numframes = unpack('i', content[5:9])[0]  # int32, number of frames
    sfrq = unpack('f', content[9:13])[0]  # float32, sample frequency
    # usercomment = merge_chars(unpack('c' * 60, content[13:73]))  # char
    # sys_comment = merge_chars(unpack('c' * 60, content[73:133]))
    # descrp_file = merge_chars(unpack('c' * 30, content[133:163]))
    # cuttoff_frq = unpack('h', content[163:165])
    coll_time = merge_chars(unpack('c' * 10, content[165:175]))
    coll_date = merge_chars(unpack('c' * 10, content[175:185]))
    # rest = merge_chars(unpack('c' * 71, content[185:256]))  # padding

    if verbose:
        print("-" * 50)
        print(f'Reading data from {filename}, recorded on {coll_date} at {coll_time} with {sfrq} Hz.')
        print("-" * 50)

    num_total = items * subitems * numframes  # total number of 'samples'
    optodata = np.array(unpack('f' * num_total, content[256:]))

    optodata[optodata <= -10e20] = np.nan  # replace NDF nan with nan
    optodata = np.reshape(optodata, (numframes, items, subitems)).T  # row = xyz, column = marker, 3rd = samples
    return optodata


def load_imu(root_dir, filenames=None):
    """
    Imports NGIMU session in nested dictionary with all devices and sensors.

    Import NGIMU session in nested dictionary with all devices with all sensors. Translated from xio-Technologies [1]_.

    Parameters
    ----------
    root_dir : str
        directory where session is located
    filenames : list, optional
        list of sensor names or single sensor name that you would like to include

    Returns
    -------
    session_data : dict
        returns nested object sensordata[device][sensor][dataframe]

    References
    ----------
    .. [1] https://github.com/xioTechnologies/NGIMU-MATLAB-Import-Logged-Data-Example

    """
    directory_contents = listdir(root_dir)  # all content in directory
    if not directory_contents:
        raise Exception("No contents in directory")
    directories = glob(f"{root_dir}/*/")  # folders of all devices
    session_data = dict()

    for sensordir in directories:  # loop through all sensor directories
        sensor_files = glob(f"{sensordir}/*.csv")
        device_name = path.split(path.split(sensordir)[0])[-1]
        device_name = "left" if "links" in device_name.lower() or "left" in device_name.lower() else device_name
        device_name = "right" if "rechts" in device_name.lower() or "right" in device_name.lower() else device_name
        device_name = "frame" if "frame" in device_name.lower() else device_name
        session_data[device_name] = dict()

        for sensor_file in sensor_files:  # loop through all csv files
            sensor_name = path.split(sensor_file)[-1].split(".csv")[0]  # sensor without path or extension

            if filenames and sensor_name not in filenames:
                continue  # skip if filenames is given and sensor not in filenames

            session_data[device_name][sensor_name] = pd.read_csv(sensor_file).drop_duplicates()
            session_data[device_name][sensor_name].rename(columns=lambda x: re.sub("[(\[].*?[)\]]", "", x)
                                                          .replace(" ", "_").lower()[:-1], inplace=True)  # remove units from name

        if not session_data[device_name]:
            raise Exception("No data was imported")
    return session_data


def load_drag_test(filename):
    """
    Loads a drag test file.

    Loads drag test data (angle and force) from an ADA .txt file.

    Parameters
    ----------
    filename : str

    Returns
    -------
    dragtest : pd.DataFrame
        DataFrame with angles and corresponding forces.

    """
    dragtest = defaultdict(list)
    with open(filename, "r") as f:
        [next(f) for _ in range(33)]  # Skip header and junk
        for line in f:
            data = line.split()
            dragtest["angle"].append(float(data[0]))
            dragtest["force"].append(float(data[1]))
    return pd.DataFrame(dragtest)


def load_optitrack(filename, include_header=False):
    """
    Loads Optitrack marker data.

    Parameters
    ----------
    filename : str
        full path to filename or filename in current path
    include_header : bool
        whether or not to include the header in the output default is False

    Returns
    -------
    marker_data : dict
        Marker data in dictionary, metadata in dictionary

    """
    # First get all the metadata
    header = {}
    with open(filename, 'r') as f:
        header["metadata"] = f.readline().replace("\n", "").split(",")
        next(f)
        header["marker_type"] = f.readline().replace("\n", "").replace(",,", "frame,time,").split(",")
        header["marker_label"] = f.readline().replace("\n", "").replace(",,", "frame,time,").split(",")
        header["marker_id"] = f.readline().replace("\n", "").replace(",,", "frame,time,").split(",")
        header["header_label1"] = f.readline().replace("\n", "").replace(",,", "frame,time,").split(",")
        header["header_label2"] = f.readline().replace("\n", "").split(",")

    # Split the metadata string into key value pairs
    metadata_dict = {}
    for key, value in zip(header["metadata"][0::2], header["metadata"][1::2]):
        if "Frame" in key:
            value = float(value)
        metadata_dict[key] = value
    header["metadata"] = metadata_dict

    # Get the markers
    first_marker = header["marker_type"].index("Marker")
    n_markers = (len(header["marker_type"]) - first_marker) // 3

    marker_data = {}
    for i in range(n_markers):
        marker_label = header["marker_label"][first_marker + i * 3]
        if "Unlabeled" in marker_label:
            marker_label = "marker_" + str(i)
        marker_columns = [first_marker + i * 3, first_marker + 1 + i * 3, first_marker + 2 + i * 3]
        marker_data[marker_label] = pd.read_csv(filename, skiprows=list(range(7)), usecols=marker_columns,
                                                names=["X", "Y", "Z"])
        marker_data[marker_label] = marker_data[marker_label][:-1]  # remove last (empty) row
    return marker_data, header if include_header else marker_data
