
import collections
import sys
import warnings

import numpy as np
from numpy import matlib as mb
import scipy as sp
import datetime as datetime
from PyQt5 import QtWidgets

import HDFRoot
from AncillaryReader import AncillaryReader
from Utilities import Utilities
from ConfigFile import ConfigFile
from RhoCorrections import RhoCorrections
from GetAnc import GetAnc
from SB_support import readSB
from Weight_RSR import Weight_RSR


class ProcessL2:
    
    @staticmethod    
    def filterData(group, badTimes):                    
        ''' Delete flagged records '''

        msg = f'Remove {group.id} Data'
        print(msg)
        Utilities.writeLogFile(msg)

        if group.id == "ANCILLARY":
            timeStamp = group.getDataset("LATITUDE").data["Datetime"]
        if group.id == "IRRADIANCE":
            timeStamp = group.getDataset("ES").data["Datetime"]
        if group.id == "RADIANCE":
            timeStamp = group.getDataset("LI").data["Datetime"]
        if group.id == "REFLECTANCE":
            timeStamp = group.getDataset("Rrs").data["Datetime"]

        startLength = len(timeStamp) 
        msg = f'   Length of dataset prior to removal {startLength} long'
        print(msg)
        Utilities.writeLogFile(msg)

        # Delete the records in badTime ranges from each dataset in the group
        finalCount = 0
        originalLength = len(timeStamp)        
        for dateTime in badTimes:
            # Need to reinitialize for each loop
            startLength = len(timeStamp)
            newTimeStamp = []

            # msg = f'Eliminate data between: {dateTime}'
            # print(msg)
            # Utilities.writeLogFile(msg)

            start = dateTime[0]
            stop = dateTime[1]

            if startLength > 0:  
                counter = 0              
                for i in range(startLength):
                    if start <= timeStamp[i] and stop >= timeStamp[i]:                      
                        group.datasetDeleteRow(i - counter)  # Adjusts the index for the shrinking arrays
                        counter += 1
                        finalCount += 1
                    else:
                        newTimeStamp.append(timeStamp[i])
            else:
                msg = 'Data group is empty. Continuing.'
                print(msg)
                Utilities.writeLogFile(msg)
            timeStamp = newTimeStamp.copy()

        if badTimes == []:
            startLength = 1 # avoids div by zero below when finalCount is 0

        for ds in group.datasets: 
            group.datasets[ds].datasetToColumns()

        msg = f'   Length of dataset after removal {originalLength-finalCount} long: {round(100*finalCount/originalLength)}% removed'
        print(msg)
        Utilities.writeLogFile(msg)
        return finalCount/originalLength
    
    @staticmethod
    def interpolateColumn(columns, wl):
        ''' Interpolate wavebands to estimate a single, unsampled waveband '''
        #print("interpolateColumn")

        # Values to return
        return_y = []

        # Column to interpolate to
        new_x = [wl]
        
        # Get wavelength values
        wavelength = []
        for k in columns:
            #print(k)
            wavelength.append(float(k))
        x = np.asarray(wavelength)

        # get the length of a column
        num = len(list(columns.values())[0])

        # Perform interpolation for each row
        for i in range(num):
            values = []
            for k in columns:
                #print("b")
                values.append(columns[k][i])
            y = np.asarray(values)
            
            new_y = sp.interpolate.interp1d(x, y)(new_x)
            return_y.append(new_y[0])

        return return_y

    
    @staticmethod
    def specQualityCheck(group, inFilePath):
        ''' Perform spectral filtering
        Calculate the STD of the normalized (at some max value) average ensemble.
        Then test each normalized spectrum against the ensemble average and STD.
        Plot results'''

        badTimes = []
        if group.id == 'IRRADIANCE':
            Data = group.getDataset("ES") 
            timeStamp = group.getDataset("ES").data["Datetime"]
            badTimes = Utilities.specFilter(inFilePath, Data, timeStamp, filterRange=[400, 700],\
                filterFactor=5, rType='Es')
            msg = f'{len(np.unique(badTimes))/len(timeStamp)*100:.1f}% of Es data flagged'
            print(msg)
            Utilities.writeLogFile(msg)  
        else:            
            Data = group.getDataset("LI")
            timeStamp = group.getDataset("LI").data["Datetime"]
            badTimes1 = Utilities.specFilter(inFilePath, Data, timeStamp, filterRange=[400, 700],\
                filterFactor=8, rType='Li')
            msg = f'{len(np.unique(badTimes1))/len(timeStamp)*100:.1f}% of Li data flagged'
            print(msg)
            Utilities.writeLogFile(msg)  

            Data = group.getDataset("LT")
            timeStamp = group.getDataset("LT").data["Datetime"]
            badTimes2 = Utilities.specFilter(inFilePath, Data, timeStamp, filterRange=[400, 700],\
                filterFactor=3, rType='Lt')
            msg = f'{len(np.unique(badTimes2))/len(timeStamp)*100:.1f}% of Lt data flagged'
            print(msg)
            Utilities.writeLogFile(msg)  

            badTimes = np.append(badTimes1,badTimes2, axis=0)
        
        if len(badTimes) == 0:
            badTimes = None
        return badTimes
            
    @staticmethod
    def ltQuality(sasGroup):   
        ''' Perform Lt Quality checking '''

        ltData = sasGroup.getDataset("LT")
        ltData.datasetToColumns()
        ltColumns = ltData.columns
        # These get popped off the columns, but restored when filterData runs datasetToColumns
        ltColumns.pop('Datetag')
        ltColumns.pop('Timetag2')
        ltDatetime = ltColumns.pop('Datetime')
                        
        badTimes = []
        for indx, dateTime in enumerate(ltDatetime):                        
            # If the Lt spectrum in the NIR is brighter than in the UVA, something is very wrong
            UVA = [350,400]
            NIR = [780,850]
            ltUVA = []
            ltNIR = []
            for wave in ltColumns:
                if float(wave) > UVA[0] and float(wave) < UVA[1]:
                    ltUVA.append(ltColumns[wave][indx])
                elif float(wave) > NIR[0] and float(wave) < NIR[1]:
                    ltNIR.append(ltColumns[wave][indx])

            if np.nanmean(ltUVA) < np.nanmean(ltNIR):
                badTimes.append(dateTime)
        
        badTimes = np.unique(badTimes)
        # Duplicate each element to a list of two elements in a list
        # BUG: This is not optimal as it creates one badTimes record for each bad
        # timestamp, rather than span of timestamps from badtimes[i][0] to badtimes[i][1]
        badTimes = np.rot90(np.matlib.repmat(badTimes,2,1), 3) 
        msg = f'{len(np.unique(badTimes))/len(ltDatetime)*100:.1f}% of spectra flagged'
        print(msg)
        Utilities.writeLogFile(msg) 

        if len(badTimes) == 0:
            badTimes = None
        return badTimes
    
    @staticmethod
    def negReflectance(reflGroup,field):   
        ''' Perform negative reflectance spectra checking '''
        # Run for entire file, not just one ensemble

        reflData = reflGroup.getDataset(field)
        # reflData.datasetToColumns()
        reflColumns = reflData.columns
        reflDate = reflColumns.pop('Datetag')
        reflTime = reflColumns.pop('Timetag2')
        # reflColumns.pop('Datetag')
        # reflColumns.pop('Timetag2')
        timeStamp = reflColumns.pop('Datetime')
                        
        badTimes = []
        for indx, timeTag in enumerate(timeStamp):                        
            # If any spectra in the vis are negative, delete the whole spectrum
            VIS = [400,700]            
            reflVIS = []
            wavelengths = []
            for wave in reflColumns:
                wavelengths.append(float(wave))
                if float(wave) > VIS[0] and float(wave) < VIS[1]:
                    reflVIS.append(reflColumns[wave][indx])
                # elif float(wave) > NIR[0] and float(wave) < NIR[1]:
                #     ltNIR.append(ltColumns[wave][indx])

            # Flag entire record for removal
            if any(item < 0 for item in reflVIS):
                badTimes.append(timeTag)

            # Set negatives to 0
            NIR = [701,max(wavelengths)]
            UV = [min(wavelengths),399]
            for wave in reflColumns:
                if ((float(wave) >= UV[0] and float(wave) < UV[1]) or \
                            (float(wave) >= NIR[0] and float(wave) <= NIR[1])) and \
                            reflColumns[wave][indx] < 0:
                    reflColumns[wave][indx] = 0
                            
        badTimes = np.unique(badTimes)
        badTimes = np.rot90(np.matlib.repmat(badTimes,2,1), 3) # Duplicates each element to a list of two elements (start, stop)
        msg = f'{len(np.unique(badTimes))/len(timeStamp)*100:.1f}% of {field} spectra flagged'
        print(msg)
        Utilities.writeLogFile(msg) 

        # # Need to add these at the beginning of the ODict        
        reflColumns['Timetag2'] = reflTime
        reflColumns['Datetag'] = reflDate
        reflColumns['Datetime'] = timeStamp
        reflColumns.move_to_end('Timetag2', last=False)
        reflColumns.move_to_end('Datetag', last=False)
        reflColumns.move_to_end('Datetime', last=False)

        reflData.columnsToDataset()        

        if len(badTimes) == 0:
            badTimes = None
        return badTimes
    
    @staticmethod
    def metQualityCheck(refGroup, sasGroup):   
        ''' Perform meteorological quality control '''

        esFlag = float(ConfigFile.settings["fL2SignificantEsFlag"])
        dawnDuskFlag = float(ConfigFile.settings["fL2DawnDuskFlag"])
        humidityFlag = float(ConfigFile.settings["fL2RainfallHumidityFlag"])     
        cloudFlag = float(ConfigFile.settings["fL2CloudFlag"])

        esData = refGroup.getDataset("ES")
        esData.datasetToColumns()
        esColumns = esData.columns
            
        dateTag = esColumns.pop('Datetag')
        timeTag2 = esColumns.pop('Timetag2')
        esTime = esColumns.pop('Datetime')

        liData = sasGroup.getDataset("LI")
        liData.datasetToColumns()
        liColumns = liData.columns
        liColumns.pop('Datetag')
        liColumns.pop('Timetag2')
        liColumns.pop('Datetime')

        ltData = sasGroup.getDataset("LT")
        ltData.datasetToColumns()
        ltColumns = ltData.columns
        ltColumns.pop('Datetag')
        ltColumns.pop('Timetag2')        
        ltColumns.pop('Datetime')    
        
        li750 = ProcessL2.interpolateColumn(liColumns, 750.0)
        es370 = ProcessL2.interpolateColumn(esColumns, 370.0)
        es470 = ProcessL2.interpolateColumn(esColumns, 470.0)
        es480 = ProcessL2.interpolateColumn(esColumns, 480.0)
        es680 = ProcessL2.interpolateColumn(esColumns, 680.0)
        es720 = ProcessL2.interpolateColumn(esColumns, 720.0)
        es750 = ProcessL2.interpolateColumn(esColumns, 750.0)
        badTimes = []
        for indx, dateTime in enumerate(esTime):                
            # Masking spectra affected by clouds (Ruddick 2006, IOCCG Protocols). 
            # The alternative to masking is to process them differently (e.g. See Ruddick_Rho)
            
            if li750[indx]/es750[indx] >= cloudFlag:
                # msg = f"Quality Check: Li(750)/Es(750) >= cloudFlag:{cloudFlag}"
                # print(msg)
                # Utilities.writeLogFile(msg)  
                badTimes.append(dateTime)


            # Threshold for significant es
            # Wernand 2002
            if es480[indx] < esFlag:
                # msg = f"Quality Check: es(480) < esFlag:{esFlag}"
                # print(msg)
                # Utilities.writeLogFile(msg)  
                badTimes.append(dateTime)

            # Masking spectra affected by dawn/dusk radiation
            # Wernand 2002
            #v = esXSlice["470.0"][0] / esXSlice["610.0"][0] # Fix 610 -> 680
            if es470[indx]/es680[indx] < dawnDuskFlag:
                # msg = f'Quality Check: ES(470.0)/ES(680.0) < dawnDuskFlag:{dawnDuskFlag}'
                # print(msg)
                # Utilities.writeLogFile(msg)  
                badTimes.append(dateTime)

            # Masking spectra affected by rainfall and high humidity
            # Wernand 2002 (940/370), Garaba et al. 2012 also uses Es(940/370), presumably 720 was developed by Wang...???
            ''' Follow up on the source of this flag'''            
            if es720[indx]/es370[indx] < humidityFlag:
                # msg = f'Quality Check: ES(720.0)/ES(370.0) < humidityFlag:{humidityFlag}'
                # print(msg)
                # Utilities.writeLogFile(msg)  
                badTimes.append(dateTime)
        
        badTimes = np.unique(badTimes)
        badTimes = np.rot90(np.matlib.repmat(badTimes,2,1), 3) # Duplicates each element to a list of two elements in a list
        msg = f'{len(np.unique(badTimes))/len(esTime)*100:.1f}% of spectra flagged'
        print(msg)
        Utilities.writeLogFile(msg)

        if len(badTimes) == 0:
            # Restore timestamps to columns (since it's not going to filterData, where it otherwise happens)
            esData.datasetToColumns()
            liData.datasetToColumns()
            ltData.datasetToColumns()
            badTimes = None
        return badTimes
    
    @staticmethod
    def columnToSlice(columns, start, end):
        ''' Take a slice of a dataset stored in columns '''

        # Each column is a time series either at a waveband for radiometer columns, or various grouped datasets for ancillary
        # Start and end are defined by the interval established in the Config (they are indexes)
        newSlice = collections.OrderedDict()
        for k in columns:
            newSlice[k] = columns[k][start:end] #up to not including end...
        return newSlice
    
    @staticmethod
    def interpAncillary(node, ancData, modData, radData):
        ''' Interpolate ancillary to radiometry and fill with model data or defaults '''

        print('Interpolating field ancillary and/or modeled ancillary data to radiometry times...')
        epoch = datetime.datetime(1970, 1, 1,tzinfo=datetime.timezone.utc)

        # Only concerned with datasets relevant to GMAO models and GUI defaults initially
        ancGroup = node.getGroup("ANCILLARY")
        # These are required and have been filled in with field, model, and/or default values
        windDataset = ancGroup.addDataset("WINDSPEED")
        aodDataset = ancGroup.addDataset("AOD")
        saltDataset = ancGroup.addDataset("SAL")
        sstDataset = ancGroup.addDataset("SST")  
        # Optional datasets; CLOUD and WAVE are basically place holders as of ver 1.0.beta;
        # (i.e. no implementation in Rho corrections)
        cloud = False
        wave = False
        if "CLOUD" in ancData.columns:
            cloudDataset = ancGroup.addDataset("CLOUD")
        if "WAVE_HT" in ancData.columns:
            waveDataset = ancGroup.addDataset("WAVE_HT")

        # Convert radData date and time to datetime and then to seconds for interpolation
        radTime = radData.data["Timetag2"].tolist()
        radSeconds = []
        radDatetime = []
        for i, radDate in enumerate(radData.data["Datetag"].tolist()):                
            radDatetime.append(Utilities.timeTag2ToDateTime(Utilities.dateTagToDateTime(radDate),radTime[i]))
            radSeconds.append((radDatetime[i]-epoch).total_seconds())
        
        if ancData:
            ancGroup.copyAttributes(ancData)    
            # These are the entire ancillary records for the cruise
            dateTime = ancData.getColumn("DATETIME")[0]
            if "WINDSPEED" in ancData.columns:
                wind = ancData.getColumn("WINDSPEED")[0]
            if "SALINITY" in ancData.columns:
                salt = ancData.getColumn("SALINITY")[0]
            if "SST" in ancData.columns:
                sst = ancData.getColumn("SST")[0]
            if "CLOUD" in ancData.columns:
                cloud = ancData.getColumn("CLOUD")[0]
            if "WAVE_HT" in ancData.columns:
                wave = ancData.getColumn("WAVE_HT")[0]
            # Convert ancillary datetime to seconds for interpolation            
            ancSeconds = [(i-epoch).total_seconds() for i in dateTime] 
        else:
            ancData = None
            msg = "Ancillary field data missing; reverting to ancillary model or defaults"
            print(msg)
            Utilities.writeLogFile(msg)

        # Test for any field ancillary data in timeframe of rad time   
        if ancData and (max(ancSeconds) <= min(radSeconds) or min(ancSeconds) >= max(radSeconds)):
            ancData = None
            msg = "Ancillary field data do not intersect radiometric data; reverting to ancillary model or defaults"
            print(msg)
            Utilities.writeLogFile(msg)  

        # Create a framework to hold combined ancillary data...
        ancInRadSeconds = []
        windFlag = []
        saltFlag = []
        sstFlag = []
        aodFlag = []        
        windInRad = []
        saltInRad = []
        sstInRad = []
        aodInRad = []
        if cloud:
            cloudFlag = []
            cloudInRad = []
        if wave:
            waveFlag = []
            waveInRad = []
        # ... the size of the radiometric dataset
        for i, value in enumerate(radSeconds):
            ancInRadSeconds.append(value)
            windFlag.append('undetermined')                   
            saltFlag.append('undetermined')                   
            sstFlag.append('undetermined')                   
            aodFlag.append('undetermined')                             
            windInRad.append(np.nan)
            saltInRad.append(np.nan)
            sstInRad.append(np.nan)
            aodInRad.append(np.nan)
            if cloud:
                cloudFlag.append('field')
                cloudInRad.append(np.nan)
            if wave:
                waveFlag.append('field')
                waveInRad.append(np.nan)
            
        # Populate with field data if possible
        if ancData:
            for i, value in enumerate(ancInRadSeconds): # step through InRad...
                idx = Utilities.find_nearest(ancSeconds,value) # ...identify from entire anc record...
                # Make sure the time difference between field anc and rad is <= 1hr
                if abs(ancSeconds[idx] - value)/60/60 < 1:  # ... and place nearest into InRad variable
                    windInRad[i] = wind[idx]                    
                    saltInRad[i] = salt[idx]
                    sstInRad[i] = sst[idx]
                    # Label the data source in the flag
                    windFlag[i] = 'field'
                    saltFlag[i] = 'field'
                    sstFlag[i] = 'field'                    
                    if cloud:
                        cloudInRad[i] = cloud[idx]
                    if wave:
                        waveInRad[i] = wave[idx]
        
        # Tallies
        msg = f'Field wind data has {np.isnan(windInRad).sum()} NaNs out of {len(windInRad)} prior to using model data'                
        print(msg)
        Utilities.writeLogFile(msg)
        msg = f'Field salt data has {np.isnan(saltInRad).sum()} NaNs out of {len(saltInRad)} prior to using model data'                
        print(msg)
        Utilities.writeLogFile(msg)
        msg = f'Field sst data has {np.isnan(sstInRad).sum()} NaNs out of {len(sstInRad)} prior to using model data'                
        print(msg)
        Utilities.writeLogFile(msg)
        msg = f'Field aod data has {np.isnan(aodInRad).sum()} NaNs out of {len(aodInRad)} prior to using model data'                
        print(msg)
        Utilities.writeLogFile(msg)
        if cloud:
            msg = f'Field aod data has {np.isnan(cloudInRad).sum()} NaNs out of {len(cloudInRad)}'
            print(msg)
            Utilities.writeLogFile(msg)
        if wave:
            msg = f'Field aod data has {np.isnan(waveInRad).sum()} NaNs out of {len(waveInRad)}'
            print(msg)
            Utilities.writeLogFile(msg)

        # Convert model data date and time to datetime and then to seconds for interpolation
        if modData is not None:                
            modTime = modData.groups[0].datasets["Timetag2"].tolist()
            modSeconds = []
            modDatetime = []
            for i, modDate in enumerate(modData.groups[0].datasets["Datetag"].tolist()):                
                modDatetime.append(Utilities.timeTag2ToDateTime(Utilities.dateTagToDateTime(modDate),modTime[i]))
                modSeconds.append((modDatetime[i]-epoch).total_seconds())  
        
        # Replace Wind, AOD NaNs with modeled data where possible. 
        # These will be within one hour of the field data.
        if modData is not None:
            msg = 'Filling in field data with model data where needed.'
            print(msg)
            Utilities.writeLogFile(msg)
            for i,value in enumerate(windInRad):
                if np.isnan(value):   
                    # msg = 'Replacing wind with model data'
                    # print(msg)
                    # Utilities.writeLogFile(msg)
                    idx = Utilities.find_nearest(modSeconds,ancInRadSeconds[i])
                    windInRad[i] = modData.groups[0].datasets['Wind'][idx]   
                    windFlag[i] = 'model'                     
            for i, value in enumerate(aodInRad):
                if np.isnan(value):
                    # msg = 'Replacing AOD with model data'
                    # print(msg)
                    # Utilities.writeLogFile(msg)
                    idx = Utilities.find_nearest(modSeconds,ancInRadSeconds[i])
                    aodInRad[i] = modData.groups[0].datasets['AOD'][idx]
                    aodFlag[i] = 'model'

        # Replace Wind, AOD, SST, and Sal with defaults where still nan
        msg = 'Filling in ancillary data with default values where still needed.'
        print(msg)
        Utilities.writeLogFile(msg)
        for i, value in enumerate(windInRad):
            if np.isnan(value):
                windInRad[i] = ConfigFile.settings["fL2DefaultWindSpeed"]
                windFlag[i] = 'default'
        for i, value in enumerate(aodInRad):
            if np.isnan(value):
                aodInRad[i] = ConfigFile.settings["fL2DefaultAOD"]
                aodFlag[i] = 'default'
        for i, value in enumerate(saltInRad):
            if np.isnan(value):
                saltInRad[i] = ConfigFile.settings["fL2DefaultSalt"]
                saltFlag[i] = 'default'
        for i, value in enumerate(sstInRad):
            if np.isnan(value):
                sstInRad[i] = ConfigFile.settings["fL2DefaultSST"]
                sstFlag[i] = 'default'

        # Populate the datasets and flags with the InRad variables
        windDataset.columns["WINDSPEED"] = windInRad
        windDataset.columns["WINDFLAG"] = windFlag
        aodDataset.columns["AOD"] = aodInRad
        aodDataset.columns["AODFLAG"] = aodFlag
        saltDataset.columns["SAL"] = saltInRad
        saltDataset.columns["SALTFLAG"] = saltFlag
        sstDataset.columns["SST"] = sstInRad
        sstDataset.columns["SSTFLAG"] = sstFlag
        if cloud:
            cloudDataset.columns["CLOUD"] = cloudInRad
            cloudDataset.columns["CLOUDFLAG"] = cloudFlag
        if wave:
            waveDataset.columns["WAVE_HT"] = waveInRad
            waveDataset.columns["WAVEFLAG"] = waveFlag

        # Convert ancillary seconds back to date/timetags ...
        ancDateTag = []
        ancTimeTag2 = []  
        radDT = []          
        for i, sec in enumerate(radSeconds):
            radDT.append(datetime.datetime.utcfromtimestamp(sec).replace(tzinfo=datetime.timezone.utc))
            ancDateTag.append(float(f'{int(radDT[i].timetuple()[0]):04}{int(radDT[i].timetuple()[7]):03}'))
            ancTimeTag2.append(float( \
                f'{int(radDT[i].timetuple()[3]):02}{int(radDT[i].timetuple()[4]):02}{int(radDT[i].timetuple()[5]):02}{int(radDT[i].microsecond/1000):03}'))
            # ancTimeTag2.append(Utilities.epochSecToDateTagTimeTag2(sec))
        
        # ... and add them to the datasets
        # dateTagDataset.columns["Datetag"] = ancDateTag
        # timeTag2Dataset.columns["Timetag2"] = ancTimeTag2
        # Move the Timetag2 and Datetag into the arrays and remove the datasets
        for ds in ancGroup.datasets:
            ancGroup.datasets[ds].columns["Datetag"] = ancDateTag
            ancGroup.datasets[ds].columns["Timetag2"] = ancTimeTag2
            ancGroup.datasets[ds].columns["Datetime"] = radDT
            ancGroup.datasets[ds].columns.move_to_end('Timetag2', last=False)
            ancGroup.datasets[ds].columns.move_to_end('Datetag', last=False)
            ancGroup.datasets[ds].columns.move_to_end('Datetime', last=False)

        windDataset.columnsToDataset()
        aodDataset.columnsToDataset()
        saltDataset.columnsToDataset()
        sstDataset.columnsToDataset()
        if cloud:
            cloudDataset.columnsToDataset()
        if wave:
            waveDataset.columnsToDataset()      
    
    @staticmethod
    def sliceAveHyper(y, hyperSlice, xSlice, xStd):
        ''' Take the slice mean of the lowest X% of hyperspectral slices '''

        hasNan = False
        # Ignore runtime warnings when array is all NaNs
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            for k in hyperSlice: # each k is a time series at a waveband.
                v = [hyperSlice[k][i] for i in y] # selects the lowest 5% within the interval window...
                mean = np.nanmean(v) # ... and averages them
                std = np.nanstd(v) # ... and the stdev for uncertainty estimates
                xSlice[k] = [mean]
                xStd[k] = [std]
                if np.isnan(mean):
                    hasNan = True
        return hasNan
    
    @staticmethod
    def sliceAveAnc(root, start, end, y, ancGroup):
        ''' Take the slice AND the mean averages of ancillary data with X% '''

        newAncGroup = root.getGroup("ANCILLARY")

        # Build a simple dictionary of datasets to reference from (input) ancGrop
        ancDict = collections.OrderedDict()
        for dsName in ancGroup.datasets:
            ds = ancGroup.datasets[dsName] # Full HDF dataset (columns & data)
            # if dsName != "Datetime" and dsName != "Datetag" and dsName != "Timetag2":                 
            ancDict[dsName] = ds # assign the data and columns within the dataset
                # ancDict[dsName].columns.pop("Datetag")
                # ancDict[dsName].columns.pop("Timetag2")
            # elif dsName == "Datetime":
            # if dsName == "LATITUDE":
            #     timeStamp = ds.columns["Datetime"]
            # timeStamp = ancDict[ds].columns.pop("Datetime")
            

        # dateSlice=ancDict['Datetag'].data[start:end+1] #up to not including end+1
        # timeSlice=ancDict['Timetag2'].data[start:end+1]

        # Stores the mean datetime
        # if len(timeStamp) > 0:
        #     epoch = datetime.datetime(1970, 1, 1,tzinfo=datetime.timezone.utc) #Unix zero hour
        #     tsSeconds = []
        #     for dt in timeStamp:
        #         tsSeconds.append((dt-epoch).total_seconds())
        #     meanSec = np.mean(tsSeconds)
        #     dateTime = datetime.datetime.utcfromtimestamp(meanSec).replace(tzinfo=datetime.timezone.utc)
        #     date = Utilities.datetime2DateTag(dateTime)
        #     time = Utilities.datetime2TimeTag2(dateTime)

        # # Stores the middle element
        # if len(dateSlice) > 0:
        #     date = dateSlice[int(len(dateSlice)/2)]
        #     time = timeSlice[int(len(timeSlice)/2)]

        for ds in ancDict: 
            # if ds != 'Datetag' and ds != 'Timetag2':
                if not newAncGroup.getDataset(ds):
                    newDS = newAncGroup.addDataset(ds)
                else:
                    newDS = newAncGroup.getDataset(ds)

                dsSlice = ProcessL2.columnToSlice(ancDict[ds].columns,start, end)                
                dsXSlice = None

                for subset in dsSlice: # several ancillary datasets are groups which will become columns (including date, time, and flags)
                    if subset == 'Datetime':
                        timeStamp = dsSlice[subset]
                        # Stores the mean datetime by converting to (and back from) epoch second
                        if len(timeStamp) > 0:
                            epoch = datetime.datetime(1970, 1, 1,tzinfo=datetime.timezone.utc) #Unix zero hour
                            tsSeconds = []
                            for dt in timeStamp:
                                tsSeconds.append((dt-epoch).total_seconds())
                            meanSec = np.mean(tsSeconds)
                            dateTime = datetime.datetime.utcfromtimestamp(meanSec).replace(tzinfo=datetime.timezone.utc)
                            date = Utilities.datetime2DateTag(dateTime)
                            time = Utilities.datetime2TimeTag2(dateTime)
                    if subset != 'Datetime' and subset != 'Datetag' and subset != 'Timetag2':
                        v = [dsSlice[subset][i] for i in y] # y is an array of indexes for the lowest X%

                        if dsXSlice is None:
                            dsXSlice = collections.OrderedDict()                        
                            # dsXSlice['Datetag'] = date.tolist()
                            # dsXSlice['Timetag2'] = time.tolist()
                            # dsXSlice['Datetime'] = Datetime.tolist()
                            dsXSlice['Datetag'] = [date]
                            dsXSlice['Timetag2'] = [time]
                            dsXSlice['Datetime'] = [dateTime]                            
                            
                        if subset.endswith('FLAG'):
                            if not subset in dsXSlice:
                                # Find the most frequest element
                                dsXSlice[subset] = []
                            dsXSlice[subset].append(Utilities.mostFrequent(v))
                        else:
                            if subset not in dsXSlice:
                                dsXSlice[subset] = []                            
                            dsXSlice[subset].append(np.mean(v)) 
                
                if subset not in newDS.columns:
                    newDS.columns = dsXSlice
                else:
                    for item in newDS.columns:
                        newDS.columns[item] = np.append(newDS.columns[item], dsXSlice[item])

                newDS.columns.move_to_end('Timetag2', last=False)
                newDS.columns.move_to_end('Datetag', last=False)
                newDS.columns.move_to_end('Datetime', last=False)
                newDS.columnsToDataset()         
       
    @staticmethod
    def calculateREFLECTANCE2(root, sasGroup, refGroup, ancGroup, start, end):
        '''Calculate the lowest X% Lt(780). Check for Nans in Li, Lt, Es, or wind. Send out for 
        meteorological quality flags. Perform glint corrections. Calculate the Rrs. Correct for NIR
        residuals.'''

        def dop(year):
            # day of perihelion            
            years = list(range(2001,2031))
            key = [str(x) for x in years]
            day = [4, 2, 4, 4, 2, 4, 3, 2, 4, 3, 3, 5, 2, 4, 4, 2, 4, 3, 3, 5, 2, 4, 4, 3, 4, 3, 3, 5, 2, 3]            
            dop = {key[i]: day[i] for i in range(0, len(key))}            
            result = dop[str(year)]
            return result

        esData = refGroup.getDataset("ES")
        liData = sasGroup.getDataset("LI")
        ltData = sasGroup.getDataset("LT") 
        
        # Copy datasets to dictionary
        esData.datasetToColumns()
        esColumns = esData.columns
        # tt2 = esColumns["Timetag2"]
        liData.datasetToColumns()
        liColumns = liData.columns        
        ltData.datasetToColumns()
        ltColumns = ltData.columns   

        # Root (new/output) groups:
        newReflectanceGroup = root.getGroup("REFLECTANCE")
        newRadianceGroup = root.getGroup("RADIANCE")
        newIrradianceGroup = root.getGroup("IRRADIANCE")        

        # If this is the first ensemble spectrum, set up the new datasets
        if not ('Rrs' in newReflectanceGroup.datasets):
            newRrsData = newReflectanceGroup.addDataset("Rrs")            
            newESData = newIrradianceGroup.addDataset("ES")        
            newLIData = newRadianceGroup.addDataset("LI")
            newLTData = newRadianceGroup.addDataset("LT") 
            newnLwData = newReflectanceGroup.addDataset("nLw")            

            newRrsDeltaData = newReflectanceGroup.addDataset("Rrs_delta")            
            newESDeltaData = newIrradianceGroup.addDataset("ES_delta")       
            newLIDeltaData = newRadianceGroup.addDataset("LI_delta")
            newLTDeltaData = newRadianceGroup.addDataset("LT_delta")
            newnLwDeltaData = newReflectanceGroup.addDataset("nLw_delta")

            if ConfigFile.settings['bL2WeightMODISA']:
                newRrsMODISAData = newReflectanceGroup.addDataset("Rrs_MODISA")
                newRrsMODISADeltaData = newReflectanceGroup.addDataset("Rrs_MODISA_delta")
                newnLwMODISAData = newReflectanceGroup.addDataset("nLw_MODISA")
                newnLwMODISADeltaData = newReflectanceGroup.addDataset("nLw_MODISA_delta")
            if ConfigFile.settings['bL2WeightMODIST']:
                newRrsMODISTData = newReflectanceGroup.addDataset("Rrs_MODIST")
                newRrsMODISTDeltaData = newReflectanceGroup.addDataset("Rrs_MODIST_delta")
                newnLwMODISTData = newReflectanceGroup.addDataset("nLw_MODIST")
                newnLwMODISTDeltaData = newReflectanceGroup.addDataset("nLw_MODIST_delta")

            if ConfigFile.settings['bL2WeightVIIRSN']:
                newRrsVIIRSNData = newReflectanceGroup.addDataset("Rrs_VIIRSN")
                newRrsVIIRSNDeltaData = newReflectanceGroup.addDataset("Rrs_VIIRSN_delta")
                newnLwVIIRSNData = newReflectanceGroup.addDataset("nLw_VIIRSN")
                newnLwVIIRSNDeltaData = newReflectanceGroup.addDataset("nLw_VIIRSN_delta")
            if ConfigFile.settings['bL2WeightVIIRSJ']:
                newRrsVIIRSJData = newReflectanceGroup.addDataset("Rrs_VIIRSJ")
                newRrsVIIRSJDeltaData = newReflectanceGroup.addDataset("Rrs_VIIRSJ_delta")
                newnLwVIIRSJData = newReflectanceGroup.addDataset("nLw_VIIRSJ")
                newnLwVIIRSJDeltaData = newReflectanceGroup.addDataset("nLw_VIIRSJ_delta")

            if ConfigFile.settings['bL2WeightSentinel3A']:
                newRrsSentinel3AData = newReflectanceGroup.addDataset("Rrs_Sentinel3A")
                newRrsSentinel3ADeltaData = newReflectanceGroup.addDataset("Rrs_Sentinel3A_delta")
                newnLwSentinel3AData = newReflectanceGroup.addDataset("nLw_Sentinel3A")
                newnLwSentinel3ADeltaData = newReflectanceGroup.addDataset("nLw_Sentinel3A_delta")
            if ConfigFile.settings['bL2WeightSentinel3B']:
                newRrsSentinel3BData = newReflectanceGroup.addDataset("Rrs_Sentinel3B")
                newRrsSentinel3BDeltaData = newReflectanceGroup.addDataset("Rrs_Sentinel3B_delta")
                newnLwSentinel3BData = newReflectanceGroup.addDataset("nLw_Sentinel3B")
                newnLwSentinel3BDeltaData = newReflectanceGroup.addDataset("nLw_Sentinel3B_delta")
        else:
            newRrsData = newReflectanceGroup.getDataset("Rrs")
            newESData = newIrradianceGroup.getDataset("ES")        
            newLIData = newRadianceGroup.getDataset("LI")
            newLTData = newRadianceGroup.getDataset("LT") 
            newnLwData = newReflectanceGroup.getDataset("nLw")

            newRrsDeltaData = newReflectanceGroup.getDataset("Rrs_delta")
            newESDeltaData = newIrradianceGroup.getDataset("ES_delta")    
            newLIDeltaData = newRadianceGroup.getDataset("LI_delta")
            newLTDeltaData = newRadianceGroup.getDataset("LT_delta")
            newnLwDeltaData = newReflectanceGroup.getDataset("nLw_delta")

            if ConfigFile.settings['bL2WeightMODISA']:
                newRrsMODISAData = newReflectanceGroup.getDataset("Rrs_MODISA")
                newRrsMODISADeltaData = newReflectanceGroup.getDataset("Rrs_MODISA_delta")
                newnLwMODISAData = newReflectanceGroup.getDataset("nLw_MODISA")
                newnLwMODISADeltaData = newReflectanceGroup.getDataset("nLw_MODISA_delta")
            if ConfigFile.settings['bL2WeightMODIST']:
                newRrsMODISTData = newReflectanceGroup.getDataset("Rrs_MODIST")
                newRrsMODISTDeltaData = newReflectanceGroup.getDataset("Rrs_MODIST_delta")
                newnLwMODISTData = newReflectanceGroup.getDataset("nLw_MODIST")
                newnLwMODISTDeltaData = newReflectanceGroup.getDataset("nLw_MODIST_delta")

            if ConfigFile.settings['bL2WeightVIIRSN']:
                newRrsVIIRSNData = newReflectanceGroup.getDataset("Rrs_VIIRSN")
                newRrsVIIRSNDeltaData = newReflectanceGroup.getDataset("Rrs_VIIRSN_delta")
                newnLwVIIRSNData = newReflectanceGroup.getDataset("nLw_VIIRSN")
                newnLwVIIRSNDeltaData = newReflectanceGroup.getDataset("nLw_VIIRSN_delta")
            if ConfigFile.settings['bL2WeightVIIRSJ']:
                newRrsVIIRSJData = newReflectanceGroup.getDataset("Rrs_VIIRSJ")
                newRrsVIIRSJDeltaData = newReflectanceGroup.getDataset("Rrs_VIIRSJ_delta")
                newnLwVIIRSJData = newReflectanceGroup.getDataset("nLw_VIIRSJ")
                newnLwVIIRSJDeltaData = newReflectanceGroup.getDataset("nLw_VIIRSJ_delta")

            if ConfigFile.settings['bL2WeightSentinel3A']:
                newRrsSentinel3AData = newReflectanceGroup.getDataset("Rrs_Sentinel3A")
                newRrsSentinel3ADeltaData = newReflectanceGroup.getDataset("Rrs_Sentinel3A_delta")
                newnLwSentinel3AData = newReflectanceGroup.getDataset("nLw_Sentinel3A")
                newnLwSentinel3ADeltaData = newReflectanceGroup.getDataset("nLw_Sentinel3A_delta")
            if ConfigFile.settings['bL2WeightSentinel3B']:
                newRrsSentinel3BData = newReflectanceGroup.getDataset("Rrs_Sentinel3B")
                newRrsSentinel3BDeltaData = newReflectanceGroup.getDataset("Rrs_Sentinel3B_delta")
                newnLwSentinel3BData = newReflectanceGroup.getDataset("nLw_Sentinel3B")
                newnLwSentinel3BDeltaData = newReflectanceGroup.getDataset("nLw_Sentinel3B_delta")

        esSlice = ProcessL2.columnToSlice(esColumns,start, end)
        liSlice = ProcessL2.columnToSlice(liColumns,start, end)
        ltSlice = ProcessL2.columnToSlice(ltColumns,start, end)
        n = len(list(ltSlice.values())[0])
    
        rhoDefault = float(ConfigFile.settings["fL2RhoSky"])
        RuddickRho = int(ConfigFile.settings["bL2RuddickRho"])
        ZhangRho = int(ConfigFile.settings["bL2ZhangRho"])
        # defaultWindSpeed = float(ConfigFile.settings["fL2DefaultWindSpeed"])
        # windSpeedMean = defaultWindSpeed # replaced later with met file, if present                         
        simpleNIRCorrection = int(ConfigFile.settings["bL2SimpleNIRCorrection"])
        simSpecNIRCorrection = int(ConfigFile.settings["bL2SimSpecNIRCorrection"])                        
        enablePercentLt = float(ConfigFile.settings["bL2EnablePercentLt"])
        percentLt = float(ConfigFile.settings["fL2PercentLt"])

        # Find the central index of the date/times to s
        timeStamp = esSlice.pop("Datetime")

        esSlice.pop("Datetag")
        esSlice.pop("Timetag2")

        liSlice.pop("Datetag")
        liSlice.pop("Timetag2")
        liSlice.pop("Datetime")

        ltSlice.pop("Datetag")
        ltSlice.pop("Timetag2")
        ltSlice.pop("Datetime")

        # Stores the mean datetime
        if len(timeStamp) > 0:
            epoch = datetime.datetime(1970, 1, 1,tzinfo=datetime.timezone.utc) #Unix zero hour
            tsSeconds = []
            for dt in timeStamp:
                tsSeconds.append((dt-epoch).total_seconds())
            meanSec = np.mean(tsSeconds)
            dateTime = datetime.datetime.utcfromtimestamp(meanSec).replace(tzinfo=datetime.timezone.utc)
            dateTag = Utilities.datetime2DateTag(dateTime)
            timeTag = Utilities.datetime2TimeTag2(dateTime)

        '''# Calculates the lowest X% (based on Hooker & Morel 2003; Hooker et al. 2002; Zibordi et al. 2002, IOCCG Protocols)
        X will depend on FOV and integration time of instrument. Hooker cites a rate of 2 Hz.
        It remains unclear to me from Hooker 2002 whether the recommendation is to take the average of the ir/radiances
        within the threshold and calculate Rrs, or to calculate the Rrs within the threshold, and then average, however IOCCG
        Protocols pretty clearly state to average the ir/radiances first, then calculate the Rrs...as done here.'''        
        x = round(n*percentLt/100) # number of retained values
        msg = f'{n} spectra in slice (ensemble).'
        print(msg)
        Utilities.writeLogFile(msg)
        
        # IS THIS NECESSARY?
        # There are sometimes only a small number of spectra in the slice,
        #  so the percent Lt estimation becomes highly questionable and is overridden.
        if n <= 5 or x == 0:
            x = n # if only 5 or fewer records retained, use them all...
        
        # Find the indexes for the lowest X%
        lt780 = ProcessL2.interpolateColumn(ltSlice, 780.0)
        index = np.argsort(lt780) # gives indexes if values were to be sorted
                
        if enablePercentLt and x > 1:
            # returns indexes of the first x values (if values were sorted); i.e. the indexes of the lowest X% of unsorted lt780
            y = index[0:x] 
            msg = f'{len(y)} spectra remaining in slice to average after filtering to lowest {percentLt}%.'
            print(msg)
            Utilities.writeLogFile(msg)
        else:
            # If Percent Lt is turned off, this will average the whole slice, and if
            # ensemble is off (set to 0), just the one spectrum will be used.
            y = index 

        # Take the mean of the lowest X%
        esXSlice = collections.OrderedDict()
        liXSlice = collections.OrderedDict()
        ltXSlice = collections.OrderedDict()  
        esXstd = collections.OrderedDict()  
        liXstd = collections.OrderedDict()  
        ltXstd = collections.OrderedDict()  

        hasNan = ProcessL2.sliceAveHyper(y, esSlice, esXSlice, esXstd)
        hasNan = ProcessL2.sliceAveHyper(y, liSlice, liXSlice, liXstd)
        hasNan = ProcessL2.sliceAveHyper(y, ltSlice, ltXSlice, ltXstd)

        # Slice average the ancillary group for the slice and the X% criteria
        # (Combines Slice and XSlice in one method)
        ProcessL2.sliceAveAnc(root, start, end, y, ancGroup)
        newAncGroup = root.getGroup("ANCILLARY") # Just populated above
        newAncGroup.attributes['Ancillary_Flags (0, 1, 2, 3)'] = ['undetermined','field','model','default']

        # Extract the last element (the current slice) for each dataset used in calculating reflectances

        # Ancillary group, unlike most groups, will have named data columns in datasets (i.e. not NONE)
        # This allows for multiple data arrays in one dataset (e.g. FLAGS)

        # These are required and will have been filled in with field data, models, and or defaults
        WINDSPEEDXSlice = newAncGroup.getDataset('WINDSPEED').data['WINDSPEED'][-1].copy()
        if isinstance(WINDSPEEDXSlice, list):
            WINDSPEEDXSlice = WINDSPEEDXSlice[0]
        AODXSlice = newAncGroup.getDataset('AOD').data['AOD'][-1].copy()
        if isinstance(AODXSlice, list):
            AODXSlice = AODXSlice[0]        
        # SOL_ELXSlice = newAncGroup.getDataset('ELEVATION').data['SUN'][-1].copy()
        # if isinstance(SOL_ELXSlice, list):
        #     SOL_ELXSlice = SOL_ELXSlice[0]
        SZAXSlice = newAncGroup.getDataset('SZA').data['SZA'][-1].copy()
        if isinstance(SZAXSlice, list):
            SZAXSlice = SZAXSlice[0]
        SSTXSlice = newAncGroup.getDataset('SST').data['SST'][-1].copy()
        if isinstance(SSTXSlice, list):
            SSTXSlice = SSTXSlice[0]
        SalXSlice = newAncGroup.getDataset('SAL').data['SAL'][-1].copy()
        if isinstance(SalXSlice, list):
            SalXSlice = SalXSlice[0]
        RelAzXSlice = newAncGroup.getDataset('REL_AZ').data['REL_AZ'][-1].copy()
        if isinstance(RelAzXSlice, list):
            RelAzXSlice = RelAzXSlice[0]

        # These are optional; in fact, there is no implementation of incorporating CLOUD or WAVEs into
        # any of the current Rho corrections yet (even though cloud IS passed to Zhang_Rho)
        if "CLOUD" in newAncGroup.datasets:
            CloudXSlice = newAncGroup.getDataset('CLOUD').data['CLOUD'].copy()
            if isinstance(CloudXSlice, list):
                CloudXSlice = CloudXSlice[0]     
        else:
            CloudXSlice = None
        if "WAVE_HT" in newAncGroup.datasets:
            WaveXSlice = newAncGroup.getDataset('WAVE_HT').data['WAVE_HT'].copy()
            if isinstance(WaveXSlice, list):
                WaveXSlice = WaveXSlice[0]        
        else:
            WaveXSlice = None
       
        if hasNan:            
            msg = 'ProcessL2.calculateREFLECTANCE2: Slice X"%" average error: Dataset all NaNs.'
            print(msg)
            Utilities.writeLogFile(msg)
            return False

        # If this is the first spectrum, add date/time, otherwise append
        # These are empty datasets from root groups.
        # Groups REFLECTANCE, IRRADIANCE, and RADIANCE are intiallized with empty datasets, but 
        # ANCILLARY is not.
        if not ("Datetag" in newRrsData.columns):            
            for gp in root.groups:
                # Ancillary is already populated.
                # The other groups only have empty (named) datasets
                if gp.id != "ANCILLARY":
                    for ds in gp.datasets:                                                
                        gp.datasets[ds].columns["Datetime"] = [dateTime] # mean of the ensemble
                        gp.datasets[ds].columns["Datetag"] = [dateTag]
                        gp.datasets[ds].columns["Timetag2"] = [timeTag]                  
        else:
            # Ancillary is already populated.
            # The other groups only have empty (named) datasets
            for gp in root.groups:
                if gp.id != "ANCILLARY":
                    for ds in gp.datasets:                                                
                        gp.datasets[ds].columns["Datetime"].append(dateTime) 
                        # mean of the ensemble
                        gp.datasets[ds].columns["Datetag"].append(dateTag)
                        gp.datasets[ds].columns["Timetag2"].append(timeTag)

        # Calculate Rho_sky
        wavebands = [*esColumns] # just grabs the keys
        wavelength = []
        wavelengthStr = []
        for k in wavebands:            
            if k != "Datetag" and k != "Datetime" and k != "Timetag2":
                wavelengthStr.append(k)
                wavelength.append(float(k))   
        # wave = [float(i) for i in wave]
        if RuddickRho:
            '''This is the Ruddick, et al. 2006 approach, which has one method for 
            clear sky, and another for cloudy. Methods of this type (i.e. not accounting
            for spectral dependence (Lee et al. 2010, Gilerson et al. 2018) or polarization
            effects (Harmel et al. 2012, Mobley 2015, Hieronumi 2016, D'Alimonte and Kajiyama 2016, 
            Foster and Gilerson 2016, Gilerson et al. 2018)) are explicitly recommended in the 
            IOCCG Protocols for Above Water Radiometry Measurements and Data Analysis (Chapter 5, Draft 2019).'''
            
            li750 = ProcessL2.interpolateColumn(liXSlice, 750.0)
            es750 = ProcessL2.interpolateColumn(esXSlice, 750.0)
            sky750 = li750[0]/es750[0]

            rhoScalar, rhoDelta = RhoCorrections.RuddickCorr(sky750, rhoDefault, WINDSPEEDXSlice)

        elif ZhangRho:     
            ''' Zhang rho is based on Zhang et al. 2017 and calculates the wavelength-dependent rho vector
            separated for sun and sky to include polarization factors.
            
            Model limitations: AOD 0 - 0.2, Solar zenith 0-60 deg, Wavelength 350-1000 nm.'''       
            rhoDict = {}
            
            # Need to limit the input for the model limitations. This will also mean cutting out Li, Lt, and Es 
            # from non-valid wavebands.
            if AODXSlice >0.2:
                msg = f'AOD = {AODXSlice}. Maximum Aerosol Optical Depth Reached. Setting to 0.2'
                print(msg)
                Utilities.writeLogFile(msg) 
                AODXSlice = 0.2
            if SZAXSlice > 60:
                # msg = f'SZA = {SZAXSlice}. Maximum Solar Zenith Reached. Setting to 60'
                # SZA is too important to the model. If it's out of bounds, skip the record...
                msg = f'SZA = {SZAXSlice}. Maximum Solar Zenith Exceeded. Aborting slice.'
                print(msg)
                Utilities.writeLogFile(msg) 
                # SZAXSlice = 60
                return False

            if min(wavelength) < 350 or max(wavelength) > 1000:
                msg = f'Wavelengths extend beyond model limits. Truncating to 350 - 1000 nm.'
                print(msg)
                Utilities.writeLogFile(msg) 
                wave_old = wavelength.copy()
                wave_list = [(i, band) for i, band in enumerate(wave_old) if (band >=350) and (band <= 1000)]
                wave_array = np.array(wave_list)
                # wavelength is now truncated to only valid wavebands for use in Zhang models
                wavelength = wave_array[:,1].tolist()
            

            rhoStructure, rhoDelta = RhoCorrections.ZhangCorr(WINDSPEEDXSlice,AODXSlice, \
                CloudXSlice,SZAXSlice,SSTXSlice,SalXSlice,RelAzXSlice,wavelength)
            rhoVector = rhoStructure['ρ']
            for i, k in enumerate(wavelength):
                rhoDict[str(k)] = rhoVector[0,i]

        rrsSlice = {}
        nLwSlice = {}
                
        # Calculate Rrs & nLw and uncertainties
        ''' No bidirectional correction is made here.....'''
        # Calculate the normalized water leaving radiance (not exact; no BRDF here)
        fp = 'Data/Thuillier_F0.sb'
        print("SB_support.readSB: " + fp)
        if not readSB(fp, no_warn=True):
            msg = "Unable to read Thuillier file. Make sure it is in SeaBASS format."
            print(msg)
            Utilities.writeLogFile(msg)  
            return None
        else:
            Thuillier = readSB(fp, no_warn=True)
            F0_raw = np.array(Thuillier.data['esun']) # uW cm^-2 nm^-1
            wv_raw = np.array(Thuillier.data['wavelength'])
            # Earth-Sun distance
            day = int(str(dateTag)[4:7])  
            year = int(str(dateTag)[0:4])  
            eccentricity = 0.01672
            dayFactor = 360/365.256363
            dayOfPerihelion = dop(year)
            dES = 1-eccentricity*np.cos(dayFactor*(day-dayOfPerihelion)) # in AU
            F0_fs = F0_raw*dES

            # Map to float for interpolation
            # wavelength  = list(map(float, list(esColumns.keys())[2:]))
            F0 = sp.interpolate.interp1d(wv_raw, F0_fs)(wavelength)
            # Use the strings for the F0 dict
            # wavelengthStr = list(esColumns.keys())[2:]
            wavelengthStr = [str(wave) for wave in wavelength]
            F0 = collections.OrderedDict(zip(wavelengthStr, F0))

        deleteKey = []
        for k in esXSlice: # loop through wavebands as key 'k'
            if (k in liXSlice) and (k in ltXSlice):
                if k not in newESData.columns:
                    newESData.columns[k] = []
                    newLIData.columns[k] = []
                    newLTData.columns[k] = []
                    newRrsData.columns[k] = []
                    newnLwData.columns[k] = []

                    newESDeltaData.columns[k] = []
                    newLIDeltaData.columns[k] = []
                    newLTDeltaData.columns[k] = []
                    newRrsDeltaData.columns[k] = []
                    newnLwDeltaData.columns[k] = []

                # At this waveband (k)
                es = esXSlice[k][0]
                li = liXSlice[k][0]
                lt = ltXSlice[k][0]
                f0  = F0[k]

                esDelta = esXstd[k][0]
                liDelta = liXstd[k][0]
                ltDelta = ltXstd[k][0]

                # Calculate the remote sensing reflectance
                if RuddickRho:                    
                    rrs = (lt - (rhoScalar * li)) / es

                    # Rrs uncertainty
                    rrsDelta = rrs * ( 
                            (liDelta/li)**2 + (rhoDelta/rhoScalar)**2 + (liDelta/li)**2 + (esDelta/es)**2 
                            )**0.5
                
                    #Calculate the normalized water leaving radiance
                    nLw = rrs*f0

                    # nLw uncertainty; no provision for F0 uncertainty here
                    nLwDelta = nLw * (
                            (liDelta/li)**2 + (rhoDelta/rhoScalar)**2 + (liDelta/li)**2 + (esDelta/es)**2
                            )**0.5
                elif ZhangRho:
                    # Only populate the valid wavelengths
                    if float(k) in wavelength:
                        rrs = (lt - (rhoDict[k] * li)) / es

                        # Rrs uncertainty
                        rrsDelta = rrs * ( 
                                (liDelta/li)**2 + (rhoDelta/rhoDict[k])**2 + (liDelta/li)**2 + (esDelta/es)**2 
                                )**0.5
                    
                        #Calculate the normalized water leaving radiance
                        nLw = rrs*f0

                        # nLw uncertainty; no provision for F0 uncertainty here
                        nLwDelta = nLw * (
                                (liDelta/li)**2 + (rhoDelta/rhoDict[k])**2 + (liDelta/li)**2 + (esDelta/es)**2
                                )**0.5
                else:
                    # Default rho
                    rhoScalar = rhoDefault
                    rhoDelta = 0.01 # Estimated for range of conditions in Mobley 1999 models; it's actually higher...

                    rrs = (lt - (rhoScalar * li)) / es

                    # Rrs uncertainty
                    rrsDelta = rrs * ( 
                            (liDelta/li)**2 + (rhoDelta/rhoScalar)**2 + (liDelta/li)**2 + (esDelta/es)**2 
                            )**0.5
                
                    #Calculate the normalized water leaving radiance
                    nLw = rrs*f0

                    # nLw uncertainty; no provision for F0 uncertainty here
                    nLwDelta = nLw * (
                            (liDelta/li)**2 + (rhoDelta/rhoScalar)**2 + (liDelta/li)**2 + (esDelta/es)**2
                            )**0.5

                newESData.columns[k].append(es)
                newLIData.columns[k].append(li)
                newLTData.columns[k].append(lt)

                newESDeltaData.columns[k].append(esDelta)
                newLIDeltaData.columns[k].append(liDelta)
                newLTDeltaData.columns[k].append(ltDelta)
                
                # Only populate valid wavelengths. Mark others for deletion
                if float(k) in wavelength:
                    newRrsDeltaData.columns[k].append(rrsDelta)
                    newnLwDeltaData.columns[k].append(nLwDelta)
                    
                    rrsSlice[k] = rrs
                    nLwSlice[k] = nLw
                else:
                    deleteKey.append(k) 
        
        # Eliminate reflectance keys/values in wavebands outside of valid set
        deleteKey = list(set(deleteKey))
        for key in deleteKey: 
            # Only need to do this for the first ensemble in file
            if key in newRrsData.columns:
                del newRrsData.columns[key]
                del newnLwData.columns[key]
                del newRrsDeltaData.columns[key]
                del newnLwDeltaData.columns[key]

        # Perfrom near-infrared residual correction to remove additional atmospheric and glint contamination
        if ConfigFile.settings["bL2PerformNIRCorrection"]:
            if simpleNIRCorrection:
                # Data show a minimum near 725; using an average from above 750 leads to negative reflectances
                # Find the minimum between 700 and 800, and subtract it from spectrum (spectrally flat)
                msg = "Perform simple residual NIR subtraction."
                print(msg)
                Utilities.writeLogFile(msg)  
                
                # rrs correction
                NIRRRs = []
                for k in rrsSlice:
                    # if float(k) >= 750 and float(k) <= 800:
                    if float(k) >= 700 and float(k) <= 800:
                        # avg += rrsSlice[k]
                        # num += 1
                        NIRRRs.append(rrsSlice[k])
                # avg /= num
                # avg = np.median(NIRRRs)
                minNIR = min(NIRRRs)
                # Subtract average from each waveband
                for k in rrsSlice:
                    # rrsSlice[k] -= avg
                    rrsSlice[k] -= minNIR
                    newRrsData.columns[k].append(rrsSlice[k])

                # nLw correction
                NIRRRs = []
                for k in nLwSlice:
                    if float(k) >= 700 and float(k) <= 800:
                        NIRRRs.append(nLwSlice[k])
                minNIR = min(NIRRRs)
                # Subtract average from each waveband
                for k in nLwSlice:
                    nLwSlice[k] -= minNIR
                    newnLwData.columns[k].append(nLwSlice[k])

            elif simSpecNIRCorrection:
                # From Ruddick 2005, Ruddick 2006 use NIR normalized similarity spectrum
                # (spectrally flat)
                msg = "Perform simulated spectrum residual NIR subtraction."
                print(msg)
                Utilities.writeLogFile(msg)  

                # These ratios are for rho = pi*Rrs
                α1 = 2.35 # 720/780 only good for rho(720)<0.03
                α2 = 1.91 # 780/870 try to avoid, data is noisy here
                threshold = 0.03

                # Retrieve Thuilliers
                # wavelength = [float(key) for key in F0.keys()]
                F0 = [value for value in F0.values()]

                # Rrs
                ρ720 = []
                x = []
                for k in rrsSlice:                
                    if float(k) >= 700 and float(k) <= 740:
                        x.append(float(k))
                        ρ720.append(np.pi*rrsSlice[k])
                if not ρ720:
                    QtWidgets.QMessageBox.critical("Error", "NIR wavebands unavailable")
                ρ1 = sp.interpolate.interp1d(x,ρ720)(720)
                F01 = sp.interpolate.interp1d(wavelength,F0)(720)
                ρ780 = []
                x = []
                for k in rrsSlice:                
                    if float(k) >= 760 and float(k) <= 800:
                        x.append(float(k))
                        ρ780.append(rrsSlice[k])
                if not ρ780:
                    QtWidgets.QMessageBox.critical("Error", "NIR wavebands unavailable")
                ρ2 = sp.interpolate.interp1d(x,ρ780)(780)
                F02 = sp.interpolate.interp1d(wavelength,F0)(780)
                ρ870 = []
                x = []
                for k in rrsSlice:                
                    if float(k) >= 850 and float(k) <= 890:
                        x.append(float(k))
                        ρ870.append(rrsSlice[k])
                if not ρ870:
                    QtWidgets.QMessageBox.critical("Error", "NIR wavebands unavailable")
                ρ3 = sp.interpolate.interp1d(x,ρ870)(870)
                F03 = sp.interpolate.interp1d(wavelength,F0)(870)
                
                if ρ1 < threshold:
                    ε = (α1*ρ2 - ρ1)/(α1-1)
                    ε = ε/np.pi # convert to Rrs units
                    εnLw = (α1*ρ2*F02/np.pi - ρ1*F01/np.pi)/(α1-1) # convert to nLw units
                    msg = f'offset(rrs) = {ε}; offset(nLw) = {εnLw}'
                    print(msg)
                    Utilities.writeLogFile(msg)  
                else:
                    msg = "SimSpec threshold tripped. Using 780/870 instead."
                    print(msg)
                    Utilities.writeLogFile(msg)  
                    ε = (α2*ρ3 - ρ2)/(α2-1)
                    ε = ε/np.pi # convert to Rrs units
                    εnLw = (α2*ρ3*F03/np.pi - ρ2*F02/np.pi)/(α2-1) # convert to nLw units              
                    msg = f'offset(rrs) = {ε}; offset(nLw) = {εnLw}'
                    print(msg)
                    Utilities.writeLogFile(msg)  
                for k in rrsSlice:
                    ''' There seems to be some confusion in the Ruddick 2005 SPIE paper.
                    By this method, ε is (and should be) negative, and so must be added 
                    rather than subtracted.''' 
                    # rrsSlice[k] -= ε
                    rrsSlice[k] += ε
                    newRrsData.columns[k].append(rrsSlice[k])
                    # nLwSlice[k] -= εnLw
                    nLwSlice[k] += εnLw
                    newnLwData.columns[k].append(nLwSlice[k])                   
        else:            
            for k in rrsSlice:
                newRrsData.columns[k].append(rrsSlice[k])
            for k in nLwSlice:
                newnLwData.columns[k].append(nLwSlice[k])   
        
        newESData.columnsToDataset() 
        newLIData.columnsToDataset()
        newLTData.columnsToDataset()
        newRrsData.columnsToDataset()
        newnLwData.columnsToDataset()

        newESDeltaData.columnsToDataset()   
        newLIDeltaData.columnsToDataset()
        newLTDeltaData.columnsToDataset()
        newRrsDeltaData.columnsToDataset()
        newnLwDeltaData.columnsToDataset()

        # Weight reflectances to satellite bands
        if ConfigFile.settings['bL2WeightMODISA']:
            print("Process MODIS Aqua Bands")
            Weight_RSR.processMODISBands(newRrsMODISAData, newRrsData, sensor='A')
            Weight_RSR.processMODISBands(newRrsMODISADeltaData, newRrsDeltaData, sensor='A')
            newRrsMODISAData.columnsToDataset()
            newRrsMODISADeltaData.columnsToDataset()
            Weight_RSR.processMODISBands(newnLwMODISAData, newnLwData, sensor='A')
            Weight_RSR.processMODISBands(newnLwMODISADeltaData, newnLwDeltaData, sensor='A')
            newnLwMODISAData.columnsToDataset()
            newnLwMODISADeltaData.columnsToDataset()
        if ConfigFile.settings['bL2WeightMODIST']:
            print("Process MODIS Terra Bands")
            Weight_RSR.processMODISBands(newRrsMODISTData, newRrsData, sensor='T')
            Weight_RSR.processMODISBands(newRrsMODISTDeltaData, newRrsDeltaData, sensor='T')
            newRrsMODISTData.columnsToDataset()
            newRrsMODISTDeltaData.columnsToDataset()
            Weight_RSR.processMODISBands(newnLwMODISTData, newnLwData, sensor='T')
            Weight_RSR.processMODISBands(newnLwMODISTDeltaData, newnLwDeltaData, sensor='T')
            newnLwMODISTData.columnsToDataset()
            newnLwMODISTDeltaData.columnsToDataset()

        if ConfigFile.settings['bL2WeightVIIRSN']:
            print("Process VIIRS SNPP Bands")
            Weight_RSR.processVIIRSBands(newRrsVIIRSNData, newRrsData, sensor='N')
            Weight_RSR.processVIIRSBands(newRrsVIIRSNDeltaData, newRrsDeltaData, sensor='N')
            newRrsVIIRSNData.columnsToDataset()
            newRrsVIIRSNDeltaData.columnsToDataset()
            Weight_RSR.processVIIRSBands(newnLwVIIRSNData, newnLwData, sensor='N')
            Weight_RSR.processVIIRSBands(newnLwVIIRSNDeltaData, newnLwDeltaData, sensor='N')
            newnLwVIIRSNData.columnsToDataset()
            newnLwVIIRSNDeltaData.columnsToDataset()
        if ConfigFile.settings['bL2WeightVIIRSJ']:
            print("Process VIIRS JPSS Bands")
            Weight_RSR.processVIIRSBands(newRrsVIIRSJData, newRrsData, sensor='J')
            Weight_RSR.processVIIRSBands(newRrsVIIRSJDeltaData, newRrsDeltaData, sensor='J')
            newRrsVIIRSJData.columnsToDataset()
            newRrsVIIRSJDeltaData.columnsToDataset()
            Weight_RSR.processVIIRSBands(newnLwVIIRSJData, newnLwData, sensor='J')
            Weight_RSR.processVIIRSBands(newnLwVIIRSJDeltaData, newnLwDeltaData, sensor='J')
            newnLwVIIRSJData.columnsToDataset()
            newnLwVIIRSJDeltaData.columnsToDataset()
        
        if ConfigFile.settings['bL2WeightSentinel3A']:
            print("Process Sentinel 3A Bands")
            Weight_RSR.processSentinel3Bands(newRrsSentinel3AData, newRrsData, sensor='A')
            Weight_RSR.processSentinel3Bands(newRrsSentinel3ADeltaData, newRrsDeltaData, sensor='A')
            newRrsSentinel3AData.columnsToDataset()
            newRrsSentinel3ADeltaData.columnsToDataset()
            Weight_RSR.processSentinel3Bands(newnLwSentinel3AData, newnLwData, sensor='A')
            Weight_RSR.processSentinel3Bands(newnLwSentinel3ADeltaData, newnLwDeltaData, sensor='A')
            newnLwSentinel3AData.columnsToDataset()
            newnLwSentinel3ADeltaData.columnsToDataset()
        
        if ConfigFile.settings['bL2WeightSentinel3B']:
            print("Process Sentinel 3B Bands")
            Weight_RSR.processSentinel3Bands(newRrsSentinel3BData, newRrsData, sensor='B')
            Weight_RSR.processSentinel3Bands(newRrsSentinel3BDeltaData, newRrsDeltaData, sensor='B')
            newRrsSentinel3BData.columnsToDataset()
            newRrsSentinel3BDeltaData.columnsToDataset()
            Weight_RSR.processSentinel3Bands(newnLwSentinel3BData, newnLwData, sensor='B')
            Weight_RSR.processSentinel3Bands(newnLwSentinel3BDeltaData, newnLwDeltaData, sensor='B')
            newnLwSentinel3BData.columnsToDataset()
            newnLwSentinel3BDeltaData.columnsToDataset()

        return True

    @staticmethod
    def calculateREFLECTANCE(root, node, gpsGroup, satnavGroup, pyrGroup, ancData, modData):
        ''' Filter out high wind and high/low SZA.
            Interpolate ancillary/model data, average intervals.
            Run meteorology quality checks.
            Pass to calculateREFLECTANCE2 for rho calcs, Rrs, NIR correction.'''

        print("calculateREFLECTANCE")                   

        # These groups have datasets with TT2 and Datetag integrated into the array
        referenceGroup = node.getGroup("IRRADIANCE")
        sasGroup = node.getGroup("RADIANCE")

        # # Filter low SZAs and high winds after interpolating model/ancillary data
        # maxWind = float(ConfigFile.settings["fL2MaxWind"]) 
        # SZAMin = float(ConfigFile.settings["fL2SZAMin"])
        # SZAMax = float(ConfigFile.settings["fL2SZAMax"])
        # if ConfigFile.settings["bL1cSolarTracker"]:
        #     SZA = 90 -satnavGroup.getDataset("ELEVATION").data["SUN"]
        #     # timeStamp = satnavGroup.getDataset("ELEVATION").data["Timetag2"]
        # else:
        if not ConfigFile.settings["bL1cSolarTracker"]:
            ancTemp = node.getGroup("TEMPORARY")
            # SZA = ancTemp.datasets["SZA"].data["NONE"].copy()    

        # If GMAO modeled data is selected in ConfigWindow, and an ancillary field data file
        # is provided in Main Window, then use the model data to fill in gaps in the field 
        # record prior to interpolating to L2 timestamps.
        #   Otherwise, interpolate just the field ancillary data, if it exists
        #   Otherwise, use the selected default values from ConfigWindow      
        # This will populate the group ANCILLARY with ancillary and/or modelled datasets
        # and/or default values, all interpolated to the radiometric data timestamps.
        #
        # This interpolation is only necessary for the ancillary datasets that require
        # either field or GMAO or GUI default values. The remaining ancillary data
        # are culled from datasets in groups already interpolated in L1E
        esData = referenceGroup.getDataset("ES") # From node, the input file
        # InterpAncillary is basically only concerned with datasets that must be filled
        # in with model or default data (wind, sst, wt, sal, cloud, wave)
        ProcessL2.interpAncillary(node, ancData, modData, esData)
        
        # Now that ancillary data has been interpolated, it is matched up with
        #  additional ancillary data (gps, solartracker, non-solartracker, etc.), all @ 1:1.
        ancGroup = node.getGroup("ANCILLARY") # from interpAncillary above...

        # At this stage, ancGroup has Datetag & Timetag2 integrated into data arrays
        # tied to AOD, SAL, SST, and WINDSPEED. These sets also have flags.
        # Add remaining datasets (as needed)
        ancGroup.addDataset('HEADING')
        ancGroup.addDataset('LATITUDE')
        ancGroup.addDataset('LONGITUDE')        
        ancGroup.addDataset('SOLAR_AZ')
        ancGroup.addDataset('SZA')        
        ancGroup.addDataset('REL_AZ')        

        # The following datasets were interpolated to the radiometry timestamps in L1E.
        # Shift them into the ANCILLARY group as needed.
        #
        # GPS Group
        # These have TT2/Datetag incorporated in arrays
        # Change their column names from NONE to something appropriate to be consistent in 
        # ancillary group going forward
        ancGroup.datasets['LATITUDE'] = gpsGroup.getDataset('LATITUDE')
        ancGroup.datasets['LATITUDE'].changeColName('NONE','LATITUDE')
        ancGroup.datasets['LONGITUDE'] = gpsGroup.getDataset('LONGITUDE')
        ancGroup.datasets['LONGITUDE'].changeColName('NONE','LONGITUDE')
        if ConfigFile.settings["bL1cSolarTracker"]:
            # These have TT2/Datetag incorporated in arrays
            ancGroup.datasets['HEADING'] = gpsGroup.getDataset('COURSE')
            ancGroup.datasets['HEADING'].changeColName('TRUE','HEADING')
            ancGroup.addDataset('SPEED')
            ancGroup.datasets['SPEED'] = gpsGroup.getDataset('SPEED')
            ancGroup.datasets['SPEED'].changeColName('NONE','SPEED')
        else:
            # NOTRACKER Group
            # These have TT2/Datetag incorporated in arrays
            ancGroup.datasets['HEADING'] = ancTemp.getDataset('HEADING')
            ancGroup.datasets['HEADING'].datasetToColumns()
            ancGroup.datasets['SZA'] = ancTemp.getDataset('SZA')
            ancGroup.datasets['SZA'].datasetToColumns()
            ancGroup.datasets['SOLAR_AZ'] = ancTemp.getDataset('SOLAR_AZ')
            ancGroup.datasets['SOLAR_AZ'].datasetToColumns()
            ancGroup.datasets['REL_AZ'] = ancTemp.getDataset('REL_AZ')
            ancGroup.datasets['REL_AZ'].datasetToColumns()

            # Done with the temporary ancillary group; delete it
            for gp in node.groups:
                if gp.id == "TEMPORARY":
                    node.removeGroup(gp)
            
        if satnavGroup:
            ancGroup.datasets['SOLAR_AZ'] = satnavGroup.getDataset('AZIMUTH')
            ancGroup.datasets['SOLAR_AZ'].changeColName('SUN','SOLAR_AZ')
            elevation = satnavGroup.getDataset('ELEVATION')
            sza = []
            for k in elevation.data["SUN"]:
                sza.append(90-k)
            elevation.data["SUN"] = sza # changed for sza
            ancGroup.datasets['SZA'] = elevation # actually sza
            ancGroup.datasets['SZA'].changeColName('SUN','SZA')
            ancGroup.datasets['HUMIDITY'] = satnavGroup.getDataset('HUMIDITY')
            ancGroup.datasets['HUMIDITY'].changeColName('NONE','HUMIDITY')
            # ancGroup.datasets['HEADING'] = satnavGroup.getDataset('HEADING') # Use GPS heading instead
            ancGroup.addDataset('PITCH')            
            ancGroup.datasets['PITCH'] = satnavGroup.getDataset('PITCH')
            ancGroup.datasets['PITCH'].changeColName('SAS','PITCH')
            ancGroup.addDataset('POINTING')
            ancGroup.datasets['POINTING'] = satnavGroup.getDataset('POINTING')
            ancGroup.datasets['POINTING'].changeColName('ROTATOR','POINTING')
            ancGroup.datasets['REL_AZ'] = satnavGroup.getDataset('REL_AZ')
            ancGroup.datasets['REL_AZ'].datasetToColumns()
            ancGroup.addDataset('ROLL')
            ancGroup.datasets['ROLL'] = satnavGroup.getDataset('ROLL')  
            ancGroup.datasets['ROLL'].changeColName('SAS','ROLL')
        
        if pyrGroup is not None:
            #PYROMETER
            ancGroup.datasets['SST_IR'] = pyrGroup.getDataset("T")  
            ancGroup.datasets['SST_IR'].datasetToColumns()
            ancGroup.datasets['SST_IR'].changeColName('IR','SST_IR')

        ''' At this stage, all datasets in all groups of node have Timetag2
            and Datetag incorporated into data arrays. Calculate and add
            Datetime to each data array. '''
        Utilities.rootAddDateTimeL2(node)

        # Filter the spectra from the entire collection before slicing the intervals

        # Lt Quality Filtering; anomalous elevation in the NIR
        if ConfigFile.settings["bL2LtUVNIR"]:
            msg = "Applying Lt quality filtering to eliminate spectra."
            print(msg)
            Utilities.writeLogFile(msg)
            # This is not well optimized for large files...
            badTimes = ProcessL2.ltQuality(sasGroup)
                
            if badTimes is not None:
                print('Removing records... Can be slow for large files')
                check = ProcessL2.filterData(referenceGroup, badTimes)
                # check is now fraction removed
                if check > 0.99:
                    msg = "Too few spectra remaining. Abort."
                    print(msg)
                    Utilities.writeLogFile(msg)
                    return False                  
                ProcessL2.filterData(sasGroup, badTimes)
                ProcessL2.filterData(ancGroup, badTimes)
                
        # Filter low SZAs and high winds after interpolating model/ancillary data
        maxWind = float(ConfigFile.settings["fL2MaxWind"]) 
        SZAMin = float(ConfigFile.settings["fL2SZAMin"])
        SZAMax = float(ConfigFile.settings["fL2SZAMax"])

        wind = ancGroup.getDataset("WINDSPEED").data["WINDSPEED"]
        SZA = ancGroup.datasets["SZA"].columns["SZA"]
        timeStamp = ancGroup.datasets["SZA"].columns["Datetime"]
        
        badTimes = None
        i=0
        start = -1
        stop = []         
        for index in range(len(SZA)):
            # Check for angles spanning north
            if SZA[index] < SZAMin or SZA[index] > SZAMax or wind[index] > maxWind:
                i += 1                              
                if start == -1:
                    if wind[index] > maxWind:
                        msg =f'High Wind: {round(wind[index])}'
                    else:
                        msg =f'Low SZA. SZA: {round(SZA[index])}'
                    print(msg)
                    Utilities.writeLogFile(msg)                                               
                    start = index
                stop = index 
                if badTimes is None:
                    badTimes = []                               
            else:                                
                if start != -1:
                    msg = f'Passed. SZA: {round(SZA[index])}, Wind: {round(wind[index])}'
                    print(msg)
                    Utilities.writeLogFile(msg)                                               
                    startstop = [timeStamp[start],timeStamp[stop]]
                    msg = f'   Flag data from TT2: {startstop[0]} to {startstop[1]}'
                    # print(msg)
                    Utilities.writeLogFile(msg)                                               
                    badTimes.append(startstop)
                    start = -1
        msg = f'Percentage of data out of SZA and Wind limits: {round(100*i/len(timeStamp))} %'
        print(msg)
        Utilities.writeLogFile(msg)

        if start != -1 and badTimes is None: # Records from a mid-point to the end are bad
            startstop = [timeStamp[start],timeStamp[stop]]
            badTimes = [startstop]

        if start==0 and stop==index: # All records are bad                           
            return False
        
        if badTimes is not None and len(badTimes) != 0:
            print('Removing records...')
            check = ProcessL2.filterData(referenceGroup, badTimes)   
            if check > 0.99:
                msg = "Too few spectra remaining. Abort."
                print(msg)
                Utilities.writeLogFile(msg)
                return False         
            ProcessL2.filterData(sasGroup, badTimes)
            ProcessL2.filterData(ancGroup, badTimes)            
                    
       # Spectral Outlier Filter
        enableSpecQualityCheck = ConfigFile.settings['bL2EnableSpecQualityCheck']
        if enableSpecQualityCheck:
            badTimes = None
            msg = "Applying spectral filtering to eliminate noisy spectra."
            print(msg)
            Utilities.writeLogFile(msg)
            inFilePath = root.attributes['In_Filepath']
            badTimes1 = ProcessL2.specQualityCheck(referenceGroup, inFilePath)
            badTimes2 = ProcessL2.specQualityCheck(sasGroup, inFilePath)
            if badTimes1 is not None and badTimes2 is not None:
                badTimes = np.append(badTimes1,badTimes2, axis=0)
            elif badTimes1 is not None:
                badTimes = badTimes1
            elif badTimes2 is not None:
                badTimes = badTimes2

            if badTimes is not None:
                print('Removing records...')
                check = ProcessL2.filterData(referenceGroup, badTimes)   
                if check > 0.99:
                    msg = "Too few spectra remaining. Abort."
                    print(msg)
                    Utilities.writeLogFile(msg)
                    return False                 
                ProcessL2.filterData(sasGroup, badTimes)
                ProcessL2.filterData(ancGroup, badTimes)       

        # Next apply the Meteorological Filter prior to slicing
        enableMetQualityCheck = int(ConfigFile.settings["bL2EnableQualityFlags"])          
        if enableMetQualityCheck:
            msg = "Applying meteorological filtering to eliminate spectra."
            print(msg)
            Utilities.writeLogFile(msg)
            badTimes = ProcessL2.metQualityCheck(referenceGroup, sasGroup)
                                  
            if badTimes is not None:
                if len(badTimes) == esData.data.size:
                    msg = "All data flagged for deletion. Abort."
                    print(msg)
                    Utilities.writeLogFile(msg)
                    return False
                print('Removing records...')
                check = ProcessL2.filterData(referenceGroup, badTimes)   
                if check > 0.99:
                    msg = "Too few spectra remaining. Abort."
                    print(msg)
                    Utilities.writeLogFile(msg)
                    return False              
                ProcessL2.filterData(sasGroup, badTimes)
                ProcessL2.filterData(ancGroup, badTimes)
        
        #
        # Break up data into time intervals, and calculate reflectance
        #
        esColumns = esData.columns
        timeStamp = esColumns["Datetime"]
        # tt2 = esColumns["Timetag2"]        
        esLength = len(list(esColumns.values())[0])
        interval = float(ConfigFile.settings["fL2TimeInterval"])    
        
        if interval == 0:
            # Here, take the complete time series
            print("No time binning. This can take a moment.")
            # Utilities.printProgressBar(0, esLength-1, prefix = 'Progress:', suffix = 'Complete', length = 50)
            for i in range(0, esLength-1):
                Utilities.printProgressBar(i+1, esLength-1, prefix = 'Progress:', suffix = 'Complete', length = 50)
                start = i
                end = i+1

                if not ProcessL2.calculateREFLECTANCE2(root, sasGroup, referenceGroup, ancGroup, start, end):
                    msg = 'ProcessL2.calculateREFLECTANCE2 unsliced failed. Abort.'
                    print(msg)
                    Utilities.writeLogFile(msg)                      
                    continue                                                      
        else:
            msg = 'Binning datasets to ensemble time interval.'
            print(msg)
            Utilities.writeLogFile(msg)    
            # Iterate over the time ensembles
            start = 0
            # endTime = Utilities.timeTag2ToSec(tt2[0]) + interval
            # endFileTime = Utilities.timeTag2ToSec(tt2[-1])
            endTime = timeStamp[0] + datetime.timedelta(0,interval)
            endFileTime = timeStamp[-1]
            timeFlag = False
            if endTime > endFileTime:
                endTime = endFileTime
                timeFlag = True # In case the whole file is shorter than the selected interval

            for i in range(0, esLength):
                # time = Utilities.timeTag2ToSec(tt2[i])
                time = timeStamp[i]
                if (time > endTime) or timeFlag: # end of increment reached
                                        
                    if timeFlag:
                        end = len(timeStamp)-1 # File shorter than interval; include all spectra
                    else:
                        endTime = time + datetime.timedelta(0,interval) # increment for the next bin loop
                        end = i # end of the slice is up to and not including...so -1 is not needed   
                    if endTime > endFileTime:
                        endTime = endFileTime                 

                    if not ProcessL2.calculateREFLECTANCE2(root, sasGroup, referenceGroup, ancGroup, start, end):
                        msg = 'ProcessL2.calculateREFLECTANCE2 with slices failed. Abort.'
                        print(msg)
                        Utilities.writeLogFile(msg)    

                        start = i                       
                        continue                          
                    start = i

                    if timeFlag:
                        break
            # Try converting any remaining
            end = esLength-1
            time = timeStamp[start]
            if time < (endTime - datetime.timedelta(0,interval)):          

                if not ProcessL2.calculateREFLECTANCE2(root,sasGroup, referenceGroup, ancGroup, start, end):
                    msg = 'ProcessL2.calculateREFLECTANCE2 ender failed. Abort.'
                    print(msg)
                    Utilities.writeLogFile(msg)    

        # Filter reflectances for negative spectra  
        ''' # 1) Any spectrum that has any negative values between
            #  380 - 700ish, remove the entire spectrum. Otherwise, 
            # set negative bands to 0.
            # This should probably wait until further analysis to see
            # how much overcorrecting is being done by the SimSpec NIR
            # correction. '''
        if ConfigFile.settings["bL2NegativeSpec"]:
            msg = "Filtering reflectance spectra for negative values."
            print(msg)
            Utilities.writeLogFile(msg)
            # newReflectanceGroup = root.groups[0]
            newReflectanceGroup = root.getGroup("REFLECTANCE")
            badTimes1 = ProcessL2.negReflectance(newReflectanceGroup, 'Rrs')
            badTimes2 = ProcessL2.negReflectance(newReflectanceGroup, 'nLw')

            if badTimes1 is not None and badTimes2 is not None:
                badTimes = np.append(badTimes1,badTimes2, axis=0)
            elif badTimes1 is not None:
                badTimes = badTimes1
            elif badTimes2 is not None:
                badTimes = badTimes2
                
            if badTimes is not None:
                print('Removing records...')               
                
                check = ProcessL2.filterData(newReflectanceGroup, badTimes)
                if check > 0.99:
                    msg = "Too few spectra remaining. Abort."
                    print(msg)
                    Utilities.writeLogFile(msg)
                    return False                  
                # ProcessL2.filterData(root.groups[1], badTimes)
                ProcessL2.filterData(root.getGroup("IRRADIANCE"), badTimes)
                # ProcessL2.filterData(root.groups[2], badTimes)
                ProcessL2.filterData(root.getGroup("RADIANCE"), badTimes)
                # ProcessL2.filterData(root.groups[3], badTimes)        
                ProcessL2.filterData(root.getGroup("ANCILLARY"), badTimes)

        return True
    
    @staticmethod
    def processL2(node, ancillaryData=None):
        '''Calculates Rrs and nLw after quality checks and filtering, glint removal, residual 
            subtraction. Weights for satellite bands, and outputs plots and SeaBASS tasetta'''

        root = HDFRoot.HDFRoot()
        root.copyAttributes(node)
        root.attributes["PROCESSING_LEVEL"] = "2"

        root.addGroup("REFLECTANCE")
        root.addGroup("IRRADIANCE")
        root.addGroup("RADIANCE")    

        pyrGroup = None
        gpsGroup = None
        satnavGroup = None
        ancGroupNoTracker = None
        for gp in node.groups:
            if gp.id.startswith("GPS"):
                gpsGroup = gp
            if gp.id == ("SOLARTRACKER"):
                satnavGroup = gp
            # if gp.id == ("SOLARTRACKER_STATUS"):
            #     satnavGroup = gp            
            if gp.id.startswith("PYROMETER"):
                pyrGroup = gp
            if gp.id.startswith("ANCILLARY_NOTRACKER"):
                # This copies the ancillary data from NOTRACKER into AncillaryData so it can be
                # interpolated as in SOLARTRACKER, at which time it is flipped back into ancGroup
                ancGroupNoTracker = gp
                ancillaryData = AncillaryReader.ancillaryFromNoTracker(gp)
                # SZA = ancGroupNoTracker.datasets["SZA"].data["NONE"]                
                temp = node.addGroup("TEMPORARY")
                temp.copy(ancGroupNoTracker)
                # for ds in ancGroupNoTracker.datasets:
                #     temp.addDataset(ds)
        node.removeGroup(ancGroupNoTracker)
                
        root.addGroup("ANCILLARY")
        node.addGroup("ANCILLARY")

        # Retrieve MERRA2 model ancillary data        
        if ConfigFile.settings["bL2pGetAnc"] ==1:         
            msg = 'Model data for Wind and AOD may be used to replace blank values. Reading in model data...'
            print(msg)
            Utilities.writeLogFile(msg)  
            modData = GetAnc.getAnc(gpsGroup)
            if modData == None:
                return None
        else:
            modData = None

        # Need to either create a new ancData object, or populate the nans in the current one with the model data
        if not ProcessL2.calculateREFLECTANCE(root, node, gpsGroup, satnavGroup, pyrGroup, ancillaryData, modData):
            return None

        root.attributes["Rrs_UNITS"] = "1/sr"
        root.attributes["nLw_UNITS"] = "uW/cm^2/nm/sr"
        
        # Check to insure at least some data survived quality checks
        if root.getGroup("REFLECTANCE").getDataset("Rrs").data is None:
            msg = "All data appear to have been eliminated from the file. Aborting."
            print(msg)
            Utilities.writeLogFile(msg)  
            return None

        # Now strip datetimes from all datasets
        for gp in root.groups:
            for dsName in gp.datasets:                
                ds = gp.datasets[dsName]
                if "Datetime" in ds.columns:
                    ds.columns.pop("Datetime")
                ds.columnsToDataset() 

        return root
