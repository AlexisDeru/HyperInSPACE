
import collections
import datetime as dt
import os


import HDFRoot
import HDFGroup
#import HDFDataset

from RawFileReader import RawFileReader


class ProcessL1a:

    # Reads a raw file and generates a L1a HDF file
    @staticmethod
    def processL1a(fp, outFilePath, calibrationMap):
        (dirpath, fileName) = os.path.split(fp)

        # Generate root header attributes
        root = HDFRoot.HDFRoot()
        root.id = "/"
        #root.attributes["PROSOFT"] = "Prosoft 9.0.4"
        #root.attributes["PROSOFT_INSTRUMENT_CONFIG"] = "testcfg"
        #root.attributes["PROSOFT_PARAMETERS_FILE_NAME"] = "test.mat"
        root.attributes["CAL_FILE_NAMES"] = ','.join(calibrationMap.keys())
        root.attributes["WAVELENGTH_UNITS"] = "nm"
        root.attributes["LU_UNITS"] = "count"
        root.attributes["ED_UNITS"] = "count"
        root.attributes["ES_UNITS"] = "count"
        root.attributes["RAW_FILE_NAME"] = fileName

        contextMap = collections.OrderedDict()

        for key in calibrationMap:
            cf = calibrationMap[key]
            gp = HDFGroup.HDFGroup()
            gp.id = cf.instrumentType
            contextMap[cf.id] = gp

        #print("contextMap:", list(contextMap.keys()))

        RawFileReader.readRawFile(fp, calibrationMap, contextMap, root)

        # Populate HDF group attributes
        for key in calibrationMap:
            cf = calibrationMap[key]
            gp = contextMap[cf.id]
            gp.attributes["InstrumentType"] = cf.instrumentType
            gp.attributes["Media"] = cf.media
            gp.attributes["MeasMode"] = cf.measMode
            gp.attributes["FrameType"] = cf.frameType
            gp.attributes["INSTRUMENT_NO"] = "1" #?? THIS SHOULD BE RETRIEVED, NOT ASSUMED
            gp.getTableHeader(cf.sensorType)
            gp.attributes["DISTANCE_1"] = "Pressure " + cf.sensorType + " 1 1 0"
            gp.attributes["DISTANCE_2"] = "Surface " + cf.sensorType + " 1 1 0"
            gp.attributes["SensorDataList"] = ", ".join([x for x in gp.datasets.keys()])
            root.groups.append(gp)


        # Generates root footer attributes
        root.attributes["PROCESSING_LEVEL"] = "1a"
        now = dt.datetime.now()
        timestr = now.strftime("%d-%b-%Y %H:%M:%S")
        root.attributes["FILE_CREATION_TIME"] = timestr
        print(timestr)

        # Converts gp.columns to numpy array
        for gp in root.groups:
            for ds in gp.datasets.values():
                ds.columnsToDataset()
        #RawFileReader.generateContext(root)

        return root
