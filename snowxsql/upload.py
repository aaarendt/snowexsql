from . data import *
from .string_management import *
import pandas as pd
import progressbar
from .utilities import get_logger, kw_in_here, avg_from_multi_sample
from subprocess import check_output, STDOUT
from geoalchemy2.elements import RasterElement, WKTElement
from geoalchemy2.shape import from_shape
import utm
import os
from os.path import join, abspath, expanduser
import numpy as np
import time


class ProfileHeader(object):
    '''
    Class for managing information stored in files headers about a snow pit
    site.

    Format of such file headers should be
    1. Each line of importance is preceded by a #
    2. Key values should be comma separated.

    e.g.
        `# PitID,COGM1C8_20200131`
        `# Date/Time,2020-01-31-15:10`

    If the file is not determined to be a site details file as indicated by the
    word site in the filename, then the all header lines except the last line
    is interpretted as header. In profile files the last line should be the
    column header which is also interpretted and stored as a class attribute

    Attributes:
        info: Dictionary containing all header information, stripped of
              unnecesary chars, all lower case, and all spaces replaced with
              underscores
        columns: Column names of data stored in csv. None for site description
                 files which is basically all one header
    '''

    # Typical names we run into that need renaming
    rename = {'location':'site_name',
             'top': 'depth',
             'height':'depth',
             'bottom':'bottom_depth',
             'density_a': 'sample_a',
             'density_b': 'sample_b',
             'density_c': 'sample_c',
             'site': 'site_id',
             'pitid': 'pit_id',
             'slope':'slope_angle',
             'weather':'weather_description',
             'sky': 'sky_cover',
             'notes':'site_notes',
             'dielectric_constant_a':'sample_a',
             'dielectric_constant_b':'sample_b',
             'dielectric_constant_c':'sample_c',
             'sample_top_height':'depth',
             'deq':'equivalent_diameter',
             'operator':'surveyors',
             'total_snow_depth':'total_depth',
             'smp_serial_number':'instrument',
              }

    # Known possible profile types anything not in here will throw an error
    available_profile_types = ['density', 'dielectric_constant', 'temperature',
                     'force', 'reflectance','sample_signal',
                     'specific_surface_area', 'deq',
                     'grain_size', 'hand_hardness', 'grain_type',
                     'manual_wetness']


    def __init__(self, filename, timezone, epsg, header_sep=',', northern_hemisphere=True, **extra_header):
        '''
        Class for managing site details information

        Args:
            filename: File for a site details file containing
            timezone: Pytz valid timezone abbreviation
            header_sep: key value pairs in header information separtor (: , etc)
            northern_hemisphere: Bool describing if the pit location is in the
                                 northern_hemisphere for converting utms coords
            extra_header: Extra header information to pass along to self.info
        '''
        self.log = get_logger(__name__)
        self.timezone = timezone
        self.northern_hemisphere = northern_hemisphere
        self.header_sep = header_sep
        self.epsg = epsg

        self.log.info('Interpretting {}'.format(filename))

        # Site location files will have no profile_type
        self.profile_type = None

        # Does our profile type have multiple samples
        self.multi_sample_profile = False

        # Read in the header into dictionary and list of columns names
        self.info, self.columns, self.header_pos = self._read(filename)

        # Extra key value paris for header information, will overwrite any duplicate information
        self.extra_header = extra_header

        # Interpret any data needing interpretation e.g. aspect
        self.interpret_data()

    def parse_column_names(self, lines):
        '''
        A flexible mnethod that attempts to find and standardize column names
        for csv data. Looks for a comma separated line with N entries == to the
        last line in the file. If an entry is found with more commas than the
        last line then we use that. This allows us to have data that doesn't
        have all the commas in the data (SSA typically missing the comma for
        veg unless it was notable)

        Assumptions:

        1. There is NOT greater than N commas in the header information prior to the column
        list

        2. The last line in file is of representative csv data

        3. The first column is numeric

        Args:
            lines: Complete list of strings from the file

        Returns:
            columns: list of column names
        '''

        # Minimum calumn size should match the last line of data (Assumption #2)
        n_columns = len(lines[-1].split(','))

        # Use these to monitor if a larger column count is found
        header_pos_options = [0, 0]
        header_lengths = [0, 0]

        for i,l in enumerate(lines):
            l = l.split(',')
            # column count
            n = len(l)

            # Grab the columns header if we see one a little bigger
            if n >= n_columns:
                header_pos_options[0] = i
                header_lengths[0] = n

            # If we find a column count larger than the current replace it
            if header_lengths[0] >= header_lengths[1]:
                header_lengths[1] = header_lengths[0]
                header_pos_options[1] = header_pos_options[0]

            # Break if we find number in the first position (Assumption #3)
            entry = l[0].replace('-','').replace('.','')
            if entry.isnumeric():
                self.log.debug('Found end of header at line {}...'.format(i))
                header_pos_options[1] = i - 1
                break


        header_pos = header_pos_options[1]

        # Parse the columns header based on the size of the last line
        raw_cols = lines[header_pos].strip('#').split(',')
        columns = [clean_str(c) for c in raw_cols]

        # Detmerine the profile type
        (self.profile_type, self.multi_sample_profile) = self.determine_profile_type(raw_cols)

        # Rename any column names to more standard ones
        columns = remap_data_names(columns, self.rename)
        self.profile_type = remap_data_names(self.profile_type, self.rename)
        return columns, header_pos

    def determine_profile_type(self, raw_columns):
        '''
        Determine the type of the profile from the raw column header. Also
        determine if this is the type of profile file that will submit more
        than one main value (e.g. hand_hardness, grain size all in the same
        file)

        Args:
            raw_columns: list of Raw text split on commas of the column names

        '''
        # Names of columns we are going to submit as main values
        profile_type = []
        multi_sample_profile = False

        # String of the columns for counting
        str_cols =  ' '.join(raw_columns).replace(' ',"_").lower()

        for ptype in self.available_profile_types:

            kw_count = str_cols.count(ptype)

            # if we have keyword match in our columns then add the type
            if kw_count > 0:
                profile_type.append(ptype)

                if kw_count > 1:
                    multi_sample_profile = True

        if profile_type:
            self.log.info('Profile types to be uploaded are: {}'
                          ''.format(', '.join(profile_type)))
        else:
            raise ValueError('Unable to determine profile type from data'
                             ' columns: {}'.format(str_cols))

        if multi_sample_profile:
            if len(profile_type) != 1:
                raise ValueError('Cannot add multi sampled columns where there is'
                                 ' more than one profile type in the data!'
                                 '\nProfile_types = {}'.format(', '.join(profile_type)))
            else:
                self.log.info('{} profile contains multiple samples for each '
                              'layer. The main value will be the average of '
                              'these samples.'.format(profile_type[0].title()))

        return profile_type, multi_sample_profile

    def _read(self, filename):
        '''
        Read in all site details file from the PITS folder under
        SnowEx2020_SQLdata If the filename has the word site in it then we
        read everything in the file. Otherwise we use this to read all the site
        data up to the header of the profile.

        E.g. Read all commented data until we see a column descriptor.

        Args:
            filename: Path to a csv containing # leading lines with site details

        Returns:
            tuple: **data** - Dictionary containing site details
                   **columns** - List of clean column names
                   **header_pos** - Index of the columns header for skiprows in read_csv
       '''

        with open(filename, encoding='latin') as fp:
            lines = fp.readlines()
            fp.close()

        # Site description files have no need for column lists
        if 'site' in filename.lower():
            self.log.info('Parsing site description header...')
            columns = None
            header_pos = None

            # Site location parses all of the file
            lines = lines[0:-1]

        # Find the column names and where it is in the file
        else:
            columns, header_pos = self.parse_column_names(lines)
            self.log.debug('Column Data found to be {} columns based on Line '
                           '{}'.format(len(columns), header_pos))

            # Only parse what we know if the header
            lines = lines[0:header_pos]


        # Clean up the lines from line returns to grab header info
        lines = [l.strip() for l in lines]
        str_data = " ".join(lines).split('#')

        # Keep track of the number of lines with # in it for data opening
        self.length = len(str_data)

        # Key value pairs are separate by some separator provided.
        data = {}

        # Collect key value pairs from the information above the column header
        for l in str_data:
            d = l.split(self.header_sep)

            # Key is always the first entry in comma sep list
            k = clean_str(d[0])

            # Avoid splitting on times
            if not 'time' in k.lower() or not 'date' in k.lower():
                value = ':'.join(d[1:])
            else:
                value = ', '.join(d[1:])

            # Assign non empty strings to dictionary
            if k and value:
                data[k] = value.strip(' ')

        # Extract datetime for separate db entries
        if 'date/time' in data.keys():
            d = pd.to_datetime(data['date/time'] + self.timezone)
        else:

            dstr = ' '.join([data['date'], data['time'],  self.timezone])
            d = pd.to_datetime(dstr)

        data['time'] = d.time()
        data['date'] = d.date()

        if 'date/time' in data.keys():
            del data['date/time']

        # Rename the info dictionary keys to more standard ones
        data = remap_data_names(data, self.rename)
        self.log.debug('Discovered {} lines of valid header info.'
                       ''.format(len(data.keys())))

        return data, columns, header_pos

    def check_integrity(self, site_info):
        '''
        Compare the attritbute info to the site dictionary to insure integrity
        between datasets. Comparisons are only done as strings currently.

        In theory the site details header should contain identical info
        to the profile header, it should only have more info than the profile
        header.

        Args:
            site_info: Dictionary containing the site details file header

        Returns:
            mismatch: Dictionary with a message about how a piece of info is
                      mismatched

        '''
        mismatch = {}

        for k, v in self.info.items():
            if k not in site_info.keys():
                mismatch[k] = 'Key not found in site details'

            else:
                if v != site_info[k]:
                    mismatch[k] = 'Profile header != Site details header'

        return mismatch


    def interpret_data(self):
        '''
        Some data inside the headers is inconsistently noted. This function
        adjusts such data to the correct format.

        Adjustments include:

        A. Add in any extra info from the extra_header dictionary, defer to info
        provided by user

        B: Rename any keys that need to be renamed

        C. Aspect is recorded either cardinal directions or degrees from north,
        should be in degrees

        D. Cast UTM attributes to correct types. Convert UTM to lat long, store both
        '''

        # Merge information, warn user about overwriting
        overwrite_keys = [k for k in self.info.keys() if k in self.extra_header.keys()]
        if overwrite_keys:
            self.log.warning('Extra header information passed will overwrite '
                             'the following information found in the file '
                             'header:\n{}'.format(', '.join(overwrite_keys)))

        self.info.update(self.extra_header)


        # Rename any awkward keys we might get
        renames = {'lat':'latitude',
                   'long':'longitude',
                   'lon':'longitude'}
        self.info = remap_data_names(self.info, renames)


        # Adjust Aspect from Cardinal to degrees from North
        if 'aspect' in self.info.keys():

            aspect = self.info['aspect']

            # Remove any degrees symbols
            aspect = aspect.replace('\u00b0','')
            aspect = aspect.replace('Â','')

            # Check for number of numeric values.
            numeric = len([True for c in aspect if c.isnumeric()])

            if numeric != len(aspect) and aspect.lower() != 'nan':
                self.log.warning('Aspect recorded for site {} is in cardinal '
                'directions, converting to degrees...'
                ''.format(self.info['site_id']))
                deg = convert_cardinal_to_degree(aspect)

        keys = self.info.keys()


        # Convert geographic details to floats
        for numeric_key in ['northing','easting','latitude','longitude']:
            if numeric_key in keys:
                self.info[numeric_key] = float(self.info[numeric_key])


        # Convert UTM coordinates to Lat long or vice versa for database storage
        if 'northing' in keys:
            self.info['utm_zone'] = \
               int(''.join([s for s in self.info['utm_zone'] if s.isnumeric()]))
            lat, long = utm.to_latlon(self.info['easting'],
                              self.info['northing'],
                              self.info['utm_zone'],
                              northern=self.northern_hemisphere)
            self.info['latitude'] = lat
            self.info['longitude'] = long

        elif 'latitude' in keys:
            easting, northing, utm_zone, letter = utm.from_latlon(
                                                self.info['latitude'],
                                                self.info['longitude'])
            self.info['easting'] = easting
            self.info['northing'] = northing
            self.info['utm_zone'] = utm_zone
        else:
            raise(ValueError('No Geographic information was'
                             'provided in the file header.'))

        # Add a geometry entry
        self.info['geom'] = WKTElement('POINT({} {})'
                            ''.format(self.info['easting'],
                                      self.info['northing']), srid=self.epsg)


class UploadProfileData():
    '''
    Class for submitting a single profile. Since layers are uploaded layer by
    layer this allows for submitting them one file at a time.
    '''
    expected_attributes = [c for c in dir(LayerData) if c[0] != '_']

    def __init__(self, profile_filename, header_sep=',', **extra_header):
        self.log = get_logger(__name__)

        self.filename = profile_filename

        timezone = extra_header['timezone']
        epsg = extra_header['epsg']
        del extra_header['timezone']
        del extra_header['epsg']

        # Read in the file header
        self._pit = ProfileHeader(profile_filename, timezone, epsg,
                              header_sep=header_sep, northern_hemisphere=True,
                              **extra_header)

        # Transfer a couple attributes for brevity
        for att in ['profile_type', 'multi_sample_profile']:
            setattr(self, att, getattr(self._pit, att))

        # Read in data
        self.df = self._read(profile_filename)


    def _read(self, profile_filename):
        '''
        Read in a profile file. Managing the number of lines to skip and
        adjusting column names

        Args:
            profile_filename: Filename containing the a manually measured
                             profile
        Returns:
            df: pd.dataframe contain csv data with standardized column names
        '''
        # header=0 because docs say to if using skiprows and columns
        df = pd.read_csv(profile_filename, header=0,
                                           skiprows= self._pit.header_pos,
                                           names=self._pit.columns,
                                           encoding='latin')

        # If SMP profile convert depth to cm
        if 'force' in df.columns:
            df['depth'] = df['depth'].div(10)

        delta = abs(df['depth'].max() - df['depth'].min())
        self.log.info('Snow Profile at {} contains {} Layers across {} cm'
                      ''.format(self._pit.info['site_id'], len(df), delta))
        return df

    def check(self, site_info):
        '''
        Checks to be applied before submitting data
        Currently checks for:

        1. Header information integrity between site info and profile headers

        Args:
            site_info: Dictionary containing all site information
        Raises:
            ValueError: If any mismatches are found
        '''

        # Ensure information matches between site details and profile headers
        mismatch = self._pit.check_integrity(site_info)

        if len(mismatch.keys()) > 0:
            self.log.error('Header Error with {}'.format(self.filename))
            for k,v in mismatch.items():
                self.log.error('\t{}: {}'.format(k, v))
                raise ValueError('Site Information Header and Profile Header '
                                 'do not agree!\n Key: {} does yields {} from '
                                 'here and {} from site info.'.format(k,
                                                             self._pit.info[k],
                                                             site_info[k]))

    def build_data(self, profile_type):
        '''
        Build out the original dataframe with the metdata to avoid doing it
        during the submission loop
        '''
        df = self.df.copy()

        # Assign all meta data to every entry to the data frame
        for k, v in self._pit.info.items():
            df[k] = v

        df['type'] = profile_type

        # Get the average if its multisample profile
        if self._pit.multi_sample_profile:
            sample_cols = [c for c in df.columns if 'sample' in c]
            df['value'] = df[sample_cols].mean(axis=1).astype(str)

        # Individual
        else:
            df['value'] = df[profile_type].astype(str)
            df = df.drop(columns=self.profile_type)

        # Drop all columns were not expecting
        drop_cols = [c for c in df.columns if c not in self.expected_attributes]
        df = df.drop(columns=drop_cols)

        return df

    def submit(self, session):
        '''
        Submit values to the db from dictionary. Manage how some profiles have
        multiple values and get submitted individual

        Args:
            session: SQLAlchemy session
        '''
        # Construct a dataframe with all metadata
        for pt in self.profile_type:
            df = self.build_data(pt)

            # Grab each row, convert it to dict and join it with site info
            for i,row in df.iterrows():
                data = row.to_dict()

                # self.log.debug('\tAdding {} for {} at {}cm'.format(value_type, data['site_id'], data['depth']))
                d = LayerData(**data)
                session.add(d)
                session.commit()

        self.log.debug('Profile Submitted!\n')

class PointDataCSV(object):
    '''
    Class for submitting whole files of point data in csv format

    '''

    # Remapping for special keywords for snowdepth measurements
    measurement_names = {'MP':'magnaprobe','M2':'mesa', 'PR':'pit ruler'}

    def __init__(self, filename, value_type, units, site_name, timezone, epsg):
        self.log = get_logger(__name__)
        self.df = self._read(filename)
        self.value_type = value_type
        self.units = units
        self.site_name = site_name
        self.timezone = timezone
        self.epsg = epsg


    def _read(self, filename):
        '''
        Read in the csv
        '''
        self.log.info('Reading in CSV data from {}'.format(filename))
        df = pd.read_csv(filename)
        return df

    def submit(self, session):
        # Loop through all the entries and add them to the db
        self.log.info('Submitting {} rows to database...'.format(len(self.df.index)))

        bar = progressbar.ProgressBar(max_value=len(self.df.index))
        for i,row in self.df.iterrows():

            # Create the data structure to pass into the interacting class attributes
            data = {'site_name':self.site_name,
                    'type':self.value_type,
                    'units':self.units}
            for k, v in row.items():
                name = k.lower()

                # Rename the tool name to work for class attributes
                if 'measurement' in name:
                    name = 'measurement_tool'
                    value = self.measurement_names[row[k]]

                # Isolate only the main name not the notes associated in header.
                else:
                    name = name.split(' ')[0]
                    value = v

                if name == 'depth':
                    name = 'value'

                data[name] = value

            # Modify date and time to reflect the timezone and then split again
            dt_str = ' '.join([str(data['date']), str(data['time']), self.timezone])
            d = pd.to_datetime(dt_str)
            data['date'] = d.date()
            data['time'] = d.time()

            # Add geometry
            data['geom'] = WKTElement('POINT({} {})'.format(data['easting'], data['northing']), srid=self.epsg)

            # Create db interaction, pass data as kwargs to class submit data
            sd = PointData(**data)
            session.add(sd)
            session.commit()
            bar.update(i)

class UploadRasterCollection(object):
    '''
    Given a folder, looks through and uploads all rasters with the matching
    the file extension
    '''
    def __init__(self, image_dir, date_time=None, description='', site_name=None, units=None, pattern='x.adf', ext='adf', epsg=0):
        self.log = get_logger(__name__)
        self.log.info('Starting raster collection upload...')
        self.image_dir = abspath(expanduser(image_dir))
        self.rasters = []

        self.meta = {'date':date_time.date(),'time':date_time.time(),
                     'description':description,
                     'site_name':site_name,
                     'units':units}
        self.epsg = epsg

        for r,ds,fs in os.walk(self.image_dir):
            for f in fs:
                if f.split('.')[-1] == ext and pattern in f:
                    self.rasters.append(join(r,f))

        self.log.info('Found {} raster in {} with ext = {} and pattern = {}.'.format(len(self.rasters), self.image_dir, ext, pattern))

    def submit(self, session):
        fails = []
        rasters_uploaded = 0
        start = time.time()

        bar = progressbar.ProgressBar(max_value=len(self.rasters))
        for i,f in enumerate(self.rasters):
            r = UploadRaster(f, self.epsg, **self.meta)
            try:
                r.submit(session)
                rasters_uploaded += 1
            except Exception as e:
                fails.append((f,e))
            bar.update(i)

        # Log errors
        if len(fails) > 0:
            self.log.error("During the upload of {} raster, {} failed.".format(len(self.rasters), len(fails)))
            for f,e in fails:
                self.log.error(e)

        self.log.info("{} / {} Rasters uploaded.".format(rasters_uploaded, len(self.rasters)))
        self.log.info('Finished! Elapsed {:d}s'.format(int(time.time() - start)))


class UploadRaster(object):
    '''
    Class for uploading a single tifs to the database
    '''
    def __init__(self, filename, epsg,  **kwargs):
        self.log = get_logger(__name__)
        self.filename = filename
        self.data = kwargs
        self.epsg = epsg

    def submit(self, session):
        '''
        Submit the data to the db using ORM
        '''
        # This produces a PSQL command
        cmd = ['raster2pgsql','-s', str(self.epsg), self.filename]
        self.log.debug('Executing: {}'.format(' '.join(cmd)))
        s = check_output(cmd, stderr=STDOUT).decode('utf-8')

        # Split the SQL command at values
        values = s.split("VALUES ('")[-1]
        values = values.split("'")[0]
        raster = RasterElement(values)
        self.data['raster'] = raster

        r = RasterData(**self.data)
        session.add(r)
        session.commit()
