from os.path import abspath, expanduser, join, basename, dirname
import os
import numpy as np
import matplotlib.pyplot as plt
import utm
from rasterio.plot import show

def read_UAVSARann(ann_file):
    '''
    .ann files describe the UAVSAR data. Use this function to read all that
    information in and return it as a dictionary

    Expected format:

    `DEM Original Pixel Spacing                     (arcsec)        = 1`

    Where this is interpretted:
    `key                     (units)        = [value]`

    Then stored in the dictionary as:

    `data[key] = {'value':value, 'units':units}`

    values that are found to be numeric and have a decimal are converted to a
    float otherwise numeric data is cast as integers. Everything else is left
    as strings.

    Args:
        ann_file: path to UAVSAR description file
    '''

    with open(ann_file) as fp:
        lines = fp.readlines()
        fp.close()

    data = {}

    # Loop through the data and parse
    for line in lines:

        # Filter out all comments and remove any line returns
        info = line.split(';')[0].strip()

        # ignore empty strings
        if info:
            d = info.split('=')
            name, value = d[0], d[1]

            # Strip and collect the units assigned to each name
            key_units = name.split('(')
            key, units = key_units[0], key_units[1]

            # Clean up tabs, spaces and line returns
            key = key.strip()
            units = units.replace(')','').strip()
            value = value.strip()

            ### Cast the values that can be to numbers ###
            if value.isnumeric():
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)

            # Assign each entry as a dictionary with value and units
            data[key] = {'value': value, 'units': units}

    return data

def readUAVSARgrd(grd_file):
    '''
    Reads in the UAVSAR .grd files. Also requires a .txt file in the same
    directory to describe the data

    Args:
        grd_file: File containing the UAVSAR data
    '''

    # Grab just the filename and make a list splitting it on periods
    fparts = basename(grd_file).split('.')
    fkey = fparts[0]

    # Get the directory the file exists in
    directory = dirname(grd_file)

    # Search local files for a matching file with .ann in its name
    ann_candidates = os.listdir(directory)
    fmatches = [f for f in ann_candidates if fkey in f and 'ann' in f]

    # If we find too many or not enough raise an exception and exit
    if len(fmatches) != 1:
        raise ValueError('Unable to find a corresponding description file to'
                         ' UAVSAR file {}'.format(grd_file))

    # Form the descriptor file name based on the grid file name, should have .ann in it
    ann_file = join(directory, fmatches[0])

    desc = read_UAVSARann(ann_file)

    nrow = desc['Ground Range Data Latitude Lines']['value']
    ncol = desc['Ground Range Data Longitude Samples']['value']

    # Find starting latitude, longitude
    lat1 = desc['Ground Range Data Starting Latitude']['value']
    lon1 = desc['Ground Range Data Starting Longitude']['value']

    # Delta latitude and longitude
    dlat = desc['Ground Range Data Latitude Spacing']['value']
    dlon = desc['Ground Range Data Longitude Spacing']['value']

    # Construct data
    z = np.fromfile(grd_file, np.int)
    print(z.shape)
    z = z.reshape(2, ncol, nrow)

    # # Create spatial coordinates
    # latitudes = np.arange(lat1, lat1 + dlat * nrow, dlat)
    # longitudes = np.arange(lon1, lon1 + dlon * nrow, dlon)
    # [LON,LAT]=meshgrid(longitudes, latitudes)
    #
    # # Set zeros to Nan
    z[z==0] = np.nan
    #
    # # Convert to UTM
    # [X, Y] = utm.from_latlon(LAT, LON)

    # Ix=~np.isnan(z);

    plt.imshow(z[0])
    plt.colorbar()
    plt.show()
# r.x=lon; r.y=lat; r.Z=Z; r.name='Grand Mesa, Feb 1, amplitude';
# crange=[0 0.5];
# hI=nanimagesc(r,Ix,crange)
#
# %%
# %figure(1);clf
# %imagesc(lon,lat,Z,[0 0.5]); colorbar
# %set(gca,'YDir','normal')
# %Z(Z==0)=NaN; % set zero values to NaN
