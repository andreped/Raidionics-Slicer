from slicer.ScriptedLoadableModule import *
import logging

import configparser
import Queue
import json
import platform
import os
import numpy
import re
import subprocess
import shutil
import threading
import csv
from collections import OrderedDict
from glob import glob
from time import sleep
from copy import deepcopy
from __main__ import qt, ctk, slicer, vtk

import SimpleITK as sitk
import sitkUtils
from src.utils.resources import SharedResources


class DeepSintefLogic:
    """
    Singleton class to have access from anywhere in the code at the various local paths where the data, or code are
    located.
    """
    __instance = None

    @staticmethod
    def getInstance():
        """ Static access method. """
        if DeepSintefLogic.__instance == None:
            DeepSintefLogic()
        return DeepSintefLogic.__instance

    def __init__(self):
        """ Virtually private constructor. """
        if DeepSintefLogic.__instance != None:
            raise Exception("This class is a singleton!")
        else:
            DeepSintefLogic.__instance = self
            self.__init_base_variables()

    def __init_base_variables(self):
        self.abort = False
        self.dockerPath = SharedResources.getInstance().docker_path
        self.file_extension_docker = '.nii.gz'
        self.logic_task = 'segmentation'  # segmentation or diagnosis for now

        # Following variables are sent to the docker images as runtime config (some options can be manually chosen by the user)
        # @TODO. Might need another singletion resources for holding that, not really relevant for the logic part of the Slicer plugin
        self.user_configuration = configparser.ConfigParser()
        self.user_configuration['Predictions'] = {}
        self.user_configuration['Predictions']['non_overlapping'] = 'true'
        self.user_configuration['Predictions']['reconstruction_method'] = 'probabilities'
        self.user_configuration['Predictions']['reconstruction_order'] = 'resample_first'
        self.user_configuration['Neuro'] = {}
        self.user_configuration['Neuro']['brain_segmentation_filename'] = ''
        self.user_configuration['Mediastinum'] = {}
        self.user_configuration['Mediastinum']['lungs_segmentation_filename'] = ''
        self.use_gpu = False

        self.user_diagnosis_configuration = configparser.ConfigParser()
        self.user_diagnosis_configuration['Default'] = {}
        self.user_diagnosis_configuration['Default']['task'] = 'neuro_diagnosis'
        self.user_diagnosis_configuration['Default']['trace'] = 'false'
        self.user_diagnosis_configuration['Default']['from_slicer'] = 'true'

    def yieldPythonGIL(self, seconds=0):
        sleep(seconds)

    def start_logic(self):
        self.main_queue = Queue.Queue()
        self.main_queue_running = False
        self.thread = threading.Thread()
        self.cmdStartLogic()

    def stop_logic(self):
        if self.main_queue_running:
            self.main_queue_stop()
        if self.thread.is_alive():
            self.thread.join()
        self.cmdStopLogic()

    def main_queue_start(self):
        """Begins monitoring of main_queue for callables"""
        self.main_queue_running = True
        # slicer.modules.DeepSintefWidget.onLogicRunStart()
        qt.QTimer.singleShot(0, self.main_queue_process)

    def main_queue_stop(self):
        """End monitoring of main_queue for callables"""
        self.main_queue_running = False
        if self.thread.is_alive():
            self.thread.join()
        # slicer.modules.DeepSintefWidget.onLogicRunStop()

    def main_queue_process(self):
        """processes the main_queue of callables"""
        try:
            while not self.main_queue.empty():
                f = self.main_queue.get_nowait()
                if callable(f):
                    f()

            if self.main_queue_running:
                # Yield the GIL to allow other thread to do some python work.
                # This is needed since pyQt doesn't yield the python GIL
                self.yieldPythonGIL(.01)
                qt.QTimer.singleShot(0, self.main_queue_process)

        except Exception as e:
            import sys
            sys.stderr.write("ModelLogic error in main_queue: \"{0}\"".format(e))

            # if there was an error try to resume
            if not self.main_queue.empty() or self.main_queue_running:
                qt.QTimer.singleShot(0, self.main_queue_process)

    def run(self, model_parameters):
        """
        Run the actual algorithm
        """
        self.cmdLogEvent('Starting the task.')
        self.start_logic()
        if self.thread.is_alive():
            import sys
            sys.stderr.write("ModelLogic is already executing!")
            return
        self.abort = False
        self.thread = threading.Thread(target=self.thread_doit(model_parameters=model_parameters))
        self.stop_logic()

    def cancel_run(self):
        self.abort = True

    def thread_doit(self, model_parameters):
        iodict = model_parameters.iodict
        inputs = model_parameters.inputs
        params = model_parameters.params
        outputs = model_parameters.outputs
        dockerName = model_parameters.dockerImageName
        modelName = model_parameters.modelName
        dataPath = model_parameters.dataPath
        widgets = model_parameters.widgets
        #try:
        go_flag = self.checkDockerImageLocalExistence(dockerName)
        if not go_flag:
            self.cmdLogEvent('The docker image does not exist, or could not be downloaded locally.\n'
                             'The selected model cannot be run.')
            return

        self.main_queue_start()
        if model_parameters.json_dict['task'] == 'Diagnosis':
            if model_parameters.json_dict['organ'] == 'Brain':
                self.user_diagnosis_configuration['Default']['task'] = 'neuro_diagnosis'
            elif model_parameters.json_dict['organ'] == 'Mediastinum':
                self.user_diagnosis_configuration['Default']['task'] = 'mediastinum_diagnosis'

        self.executeDocker(dockerName, modelName, dataPath, iodict, inputs, outputs, params, widgets)
        if not self.abort:
            self.updateOutput(iodict, outputs, widgets)
            # self.main_queue_stop()
            self.stop_logic()
            # self.cmdEndEvent()

        '''
        except Exception as e:
            msg = e.message
            qt.QMessageBox.critical(slicer.util.mainWindow(), "Exception during execution of ", msg)
            slicer.modules.DeepSintefWidget.applyButton.enabled = True
            slicer.modules.DeepSintefWidget.progress.hide = True
            self.abort = True
            self.yieldPythonGIL()
        '''

    def cmdStartLogic(self):
        if hasattr(slicer.modules, 'DeepSintefWidget'):
            widget = slicer.modules.DeepSintefWidget
            widget.on_logic_event_start(self.logic_task)

    def cmdStopLogic(self):
        if hasattr(slicer.modules, 'DeepSintefWidget'):
            widget = slicer.modules.DeepSintefWidget
            widget.on_logic_event_end(self.logic_task)

    def cmdProgressEvent(self, progress, line):
        if hasattr(slicer.modules, 'DeepSintefWidget'):
            widget = slicer.modules.DeepSintefWidget
            widget.on_logic_event_progress(self.logic_task, progress, line)

    def cmdLogEvent(self, line):
        if hasattr(slicer.modules, 'DeepSintefWidget'):
            widget = slicer.modules.DeepSintefWidget
            widget.on_logic_log_event(line)

    def cmdCheckAbort(self, p):
        if self.abort:
            p.kill()
            # self.cmdAbortEvent()

    def checkDockerDaemon(self):
        cmd = list()
        cmd.append(self.dockerPath)
        cmd.append('ps')
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        slicer.app.processEvents()
        line = p.stdout.readline()
        if line[:9] == 'CONTAINER':
            return True
        return False

    def checkDockerImageLocalExistence(self, docker_image_name):
        # Verify if the docker image exists on disk, or ask to download it
        cmd_docker = ['docker', 'image', 'inspect', docker_image_name]
        p = subprocess.Popen(cmd_docker, stdout=subprocess.PIPE)
        res_lines = ""
        while True:
            slicer.app.processEvents()
            line = p.stdout.readline()
            if not line:
                break
            res_lines = res_lines + '/n' + line

        # If the image has not been found, attempt to download it
        if 'Error: No such image' in res_lines:
            cmd_docker = ['docker', 'image', 'pull', docker_image_name]
            p = subprocess.Popen(cmd_docker, stdout=subprocess.PIPE)
            cmd_docker = ['docker', 'image', 'inspect', docker_image_name]
            p = subprocess.Popen(cmd_docker, stdout=subprocess.PIPE)
            res_lines = ""
            while True:
                slicer.app.processEvents()
                line = p.stdout.readline()
                if not line:
                    break
                res_lines = res_lines + '/n' + line

            # If the image could not be downloaded -- abort
            if 'Error: No such image' in res_lines:
                return False

        return True

    def executeDocker(self, dockerName, modelName, dataPath, iodict, inputs, outputs, params, widgets):
        try:
            assert self.checkDockerDaemon(), "Docker Daemon is not running"
        except Exception as e:
            print(e.message)
            self.abort = True

        modules = slicer.modules
        if hasattr(modules, 'DeepSintefWidget'):
            widgetPresent = True
        else:
            widgetPresent = False

        # if widgetPresent:
        #     self.cmdStartEvent()
        inputDict = dict()
        outputDict = dict()
        paramDict = dict()
        for item in iodict:
            if iodict[item]["iotype"] == "input":
                if iodict[item]["type"] == "volume":
                    # print(inputs[item])
                    input_node_name = inputs[item].GetName()
                    #try:
                    img = sitk.ReadImage(sitkUtils.GetSlicerITKReadWriteAddress(input_node_name))
                    fileName = item + self.file_extension_docker
                    inputDict[item] = fileName
                    sitk.WriteImage(img, str(os.path.join(SharedResources.getInstance().data_path, fileName)))
                    #except Exception as e:
                    #    print(e.message)
                elif iodict[item]["type"] == "configuration":
                    with open(SharedResources.getInstance().user_config, 'w') as configfile:
                        self.user_configuration.write(configfile)
                    with open(SharedResources.getInstance().diagnosis_config, 'w') as configfile:
                        self.user_diagnosis_configuration.write(configfile)
                    #inputDict[item] = configfile
            elif iodict[item]["iotype"] == "output":
                if iodict[item]["type"] == "volume":
                    outputDict[item] = item
                    nodes = slicer.util.getNodes(outputDict[item])
                    if len(nodes) == 0:
                        # if iodict[item]["voltype"] == "Segmentation":
                        #     node = slicer.vtkMRMLSegmentationNode()
                        # else:
                        #     node = slicer.vtkMRMLLabelMapVolumeNode()
                        node = slicer.vtkMRMLLabelMapVolumeNode()
                        node.SetName(outputDict[item])
                        slicer.mrmlScene.AddNode(node)
                        node.CreateDefaultDisplayNodes()
                        if iodict[item]["voltype"] == "LabelMap":
                            imageData = vtk.vtkImageData()
                            imageData.SetDimensions((150, 150, 150))
                            imageData.AllocateScalars(vtk.VTK_SHORT, 1)
                            node.SetAndObserveImageData(imageData)
                        # elif iodict[item]["voltype"] == "Segmentation":
                        #     segmentData = vtk.vtkSegmentation()
                        #     segmentData.AddEmptySegment()
                        #     node.SetAndObserveSegmentation(segmentData)
                        outputs[item] = node

                        # @TODO. select the correct item in the combobox upon creation
                        # combobox_widget = widgets[[x.accessibleName == item + '_combobox' for x in widgets].index(True)]
                        # combobox_widget.setCurrentText(item)

                elif iodict[item]["type"] == "point_vec":
                    outputDict[item] = item + '.fcsv'
                elif iodict[item]["type"] == "text":
                    outputDict[item] = item + '.txt'
                else:
                    paramDict[item] = str(params[item])
            elif iodict[item]["iotype"] == "parameter":
                paramDict[item] = str(params[item])

        dataPath = '/home/ubuntu/resources'
        if self.logic_task == 'diagnosis':
            dataPath = '/home/ubuntu/sintef-segmenter/resources'

        self.cmdLogEvent('Docker run command:')

        cmd = list()
        cmd.append(self.dockerPath)
        cmd.extend(('run', '-t', '-v'))
        # if self.use_gpu:
        #     cmd.append(' --runtime=nvidia ')
        #cmd.append(TMP_PATH + ':' + dataPath)
        cmd.append(SharedResources.getInstance().resources_path + ':' + dataPath)
        cmd.append(dockerName)
        if self.logic_task == 'segmentation':
            cmd.append('--' + 'Task')
            cmd.append('segmentation')  #@TODO. Should consider including that in model_parameters, might be diagnosis in the future

        arguments = []
        for key in inputDict.keys():
            cmd.append('--' + 'Input')
            cmd.append(dataPath + '/data/' + inputDict[key])
            # arguments.append(' --Input ' + dataPath + '/data/' + inputDict[key])
        # for key in outputDict.keys():
        #     arguments.append(key + ' ' + dataPath + '/' + outputDict[key])
        cmd.append('--' + 'Output')
        cmd.append(dataPath + '/output/')
        # if self.logic_task == 'diagnosis':
        #     cmd.append(dataPath + '/output/')
        # else:
        #     cmd.append(dataPath + '/data/' + 'DeepSintefOutput')
        # arguments.append(' --Output' + ' ' + dataPath + '/data/' + 'DeepSintefOutput')
        # arguments.append(dataPath + '/' + inputDict['InputVolume'])
        # arguments.append(dataPath + '/' + outputDict['OutputLabel'])
        if modelName:
            cmd.append('--' + 'Model')
            cmd.append(modelName)
            # arguments.append(' --Model ' + modelName)
        else:
            pass # Should break?

        if self.use_gpu:  # Should maybe let the user choose which GPU, if multiple on a machine?
            cmd.append('--' + 'GPU')
            cmd.append('0')

        if self.logic_task == 'diagnosis':
            cmd.append('--' + 'Config')
            cmd.append(dataPath + '/data/' + 'diagnosis_config.ini')

        self.cmdLogEvent(cmd)

        # TODO: add a line to check wether the docker image is present or not. If not ask user to download it.
        # try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        progress = 0
        # print('executing')
        while True:
            progress += 0.15
            slicer.app.processEvents()
            self.cmdCheckAbort(p)
            line = p.stdout.readline()
            if not line:
                break

            if widgetPresent:
                self.cmdLogEvent(line)
                self.cmdProgressEvent(progress, line)
            # print(line)

    def updateOutput(self, iodict, outputs, widgets):
        # print('updateOutput method')
        output_volume_files = dict()
        output_fiduciallist_files = dict()
        output_text_files = dict()
        self.output_raw_values = dict()
        created_files = []
        for _, _, files in os.walk(SharedResources.getInstance().output_path):
            for f, file in enumerate(files):
                created_files.append(file)
            break

        for item in iodict:
            if iodict[item]["iotype"] == "output":
                if iodict[item]["type"] == "volume":
                    #fileName = str(os.path.join(TMP_PATH, created_files[nb_class]))
                    fileName = str(os.path.join(SharedResources.getInstance().output_path, created_files[[item in x for x in created_files].index(True)]))
                    output_volume_files[item] = fileName
                if iodict[item]["type"] == "point_vec":
                    fileName = str(os.path.join(SharedResources.getInstance().output_path, item + '.fcsv'))
                    output_fiduciallist_files[item] = fileName
                if iodict[item]["type"] == "text":
                    fileName = str(os.path.join(SharedResources.getInstance().output_path, item + '.txt'))
                    output_text_files[item] = fileName

        for output_volume in output_volume_files.keys():
            result = sitk.ReadImage(output_volume_files[output_volume])
            # print(result.GetPixelIDTypeAsString())
            self.output_raw_values[output_volume] = deepcopy(sitk.GetArrayFromImage(result))
            output_node = outputs[output_volume]
            output_node_name = output_node.GetName()
            # if iodict[output_volume]["voltype"] == 'LabelMap':
            nodeWriteAddress = sitkUtils.GetSlicerITKReadWriteAddress(output_node_name)
            self.display_port = nodeWriteAddress
            sitk.WriteImage(result, nodeWriteAddress)
            applicationLogic = slicer.app.applicationLogic()
            selectionNode = applicationLogic.GetSelectionNode()

            outputLabelMap = True
            if outputLabelMap:
                selectionNode.SetReferenceActiveLabelVolumeID(output_node.GetID())
            else:
                selectionNode.SetReferenceActiveVolumeID(output_node.GetID())

            applicationLogic.PropagateVolumeSelection(0)
            applicationLogic.FitSliceToAll()
            # if iodict[output_volume]["voltype"] == 'Segmentation':
            #     seg = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode')
            #     slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(output_node, seg)
            #     seg.CreateClosedSurfaceRepresentation()

        for fiduciallist in output_fiduciallist_files.keys():
            # information about loading markups: https://www.slicer.org/wiki/Documentation/Nightly/Modules/Markups
            output_node = outputs[fiduciallist]
            _, node = slicer.util.loadMarkupsFiducialList(output_fiduciallist_files[fiduciallist], True)
            output_node.Copy(node)
            scene = slicer.mrmlScene
            # todo: currently due to a bug in markups module removing the node will create some unexpected behaviors
            # reported bug reference: https://issues.slicer.org/view.php?id=4414
            # scene.RemoveNode(node)
        for text_key in output_text_files.keys():
            text_file = output_text_files[text_key]
            f = open(text_file, 'r')
            current_text = f.read()
            current_widget = widgets[[x.accessibleName == text_key for x in widgets].index(True)]
            current_widget.setPlainText(current_text)
            f.close()

    def generate_segmentations_from_labelmaps(self, model_parameters):
        iodict = model_parameters.iodict
        outputs = model_parameters.outputs

        # If segments were created before, erase them and recompute?
        # model_parameters.segmentations = dict()

        for output in outputs.keys():
            output_node = outputs[output]
            seg_node = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode')
            slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(output_node, seg_node)
            seg_node.CreateClosedSurfaceRepresentation()
            seg_node.SetName(output)

            if 'color' in iodict[output]:
                detailed_color = [int(x) for x in iodict[output]['color'].split(',')]
                detailed_color = [x / 255. for x in detailed_color]
                #seg_node.SetColor(detailed_color[0], detailed_color[1], detailed_color[2])
                seg_node.GetSegmentation().GetNthSegment(0).SetColor(detailed_color[0], detailed_color[1], detailed_color[2])

            if 'description' in iodict[output] and iodict[output]['description'] == 'True':
                lobes_info = []
                csv_filename = str(os.path.join(SharedResources.getInstance().output_path, output + '_description.csv'))
                file = open(csv_filename, 'r')
                csvfile = csv.DictReader(file)
                for row in csvfile:
                    lobes_info.append(dict(row))
                file.close()

                for l in lobes_info:
                    seg_node.GetSegmentation().GetNthSegment(int(l['label'])).SetName(l['text'])
            # model_parameters.segmentations[output] = seg

# class DeepSintefLogic:
#     """This class should implement all the actual
#     computation done by your module.  The interface
#     should be such that other python code can import
#     this class and make use of the functionality without
#     requiring an instance of the Widget
#     """
#
#     def __init__(self):
#         self.main_queue = Queue.Queue()
#         self.main_queue_running = False
#         self.thread = threading.Thread()
#         self.abort = False
#         modules = slicer.modules
#         if hasattr(modules, 'DeepSintefWidget'):
#             self.dockerPath = slicer.modules.DeepSintefWidget.dockerPath.currentPath
#         else:
#             if platform.system() == 'Darwin':
#                 defualt_path = '/usr/local/bin/docker'
#                 self.setDockerPath(defualt_path)
#             elif platform.system() == 'Linux':
#                 defualt_path = '/usr/bin/docker'
#                 self.setDockerPath(defualt_path)
#             elif platform.system() == 'Windows':
#                 defualt_path = "C:/Program Files/Docker/Docker/resources/bin/docker.exe"
#                 self.setDockerPath(defualt_path)
#             else:
#                 print('could not determine system type')
#         self.file_extension_docker = '.nii.gz'
#         self.user_configuration = configparser.ConfigParser()
#         self.user_configuration['Predictions'] = {}
#         self.user_configuration['Predictions']['non_overlapping'] = 'true'
#         self.user_configuration['Predictions']['reconstruction_method'] = 'thresholding'
#         self.user_configuration['Predictions']['reconstruction_order'] = 'resample_second'
#
#         self.use_gpu = False
#         self.current_threshold_class_index = None
#         self.current_class_thresholds = None
#
#     def __del__(self):
#         if self.main_queue_running:
#             self.main_queue_stop()
#         if self.thread.is_alive():
#             self.thread.join()
#
#     def setDockerPath(self, path):
#         self.dockerPath = path
#
#     def yieldPythonGIL(self, seconds=0):
#         sleep(seconds)
#
#     def cmdCheckAbort(self, p):
#         if self.abort:
#             p.kill()
#             self.cmdAbortEvent()
#
#     def cmdStartEvent(self):
#         if hasattr(slicer.modules, 'DeepSintefWidget'):
#             widget = slicer.modules.DeepSintefWidget
#             widget.onLogicEventStart()
#         self.yieldPythonGIL()
#
#     def cmdProgressEvent(self, progress):
#         if hasattr(slicer.modules, 'DeepSintefWidget'):
#             widget = slicer.modules.DeepSintefWidget
#             widget.onLogicEventProgress(progress)
#         self.yieldPythonGIL()
#
#     def cmdAbortEvent(self):
#         if hasattr(slicer.modules, 'DeepSintefWidget'):
#             widget = slicer.modules.DeepSintefWidget
#             widget.onLogicEventAbort()
#         self.yieldPythonGIL()
#
#     def cmdEndEvent(self):
#         if hasattr(slicer.modules, 'DeepSintefWidget'):
#             widget = slicer.modules.DeepSintefWidget
#             widget.onLogicEventEnd()
#         self.yieldPythonGIL()
#
#     def checkDockerDaemon(self):
#         cmd = list()
#         cmd.append(self.dockerPath)
#         cmd.append('ps')
#         p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
#         slicer.app.processEvents()
#         line = p.stdout.readline()
#         if line[:9] == 'CONTAINER':
#             return True
#         return False
#
#     def notifyChangedFileExtensionForDocker(self, new_extension):
#         self.file_extension_docker = new_extension
#
#     def executeDocker(self, dockerName, modelName, dataPath, iodict, inputs, outputs, params):
#         try:
#             assert self.checkDockerDaemon(), "Docker Daemon is not running"
#         except Exception as e:
#             print(e.message)
#             self.abort = True
#
#         modules = slicer.modules
#         if hasattr(modules, 'DeepSintefWidget'):
#             widgetPresent = True
#         else:
#             widgetPresent = False
#
#         if widgetPresent:
#             self.cmdStartEvent()
#         inputDict = dict()
#         outputDict = dict()
#         paramDict = dict()
#         for item in iodict:
#             if iodict[item]["iotype"] == "input":
#                 if iodict[item]["type"] == "volume":
#                     # print(inputs[item])
#                     input_node_name = inputs[item].GetName()
#                     #try:
#                     img = sitk.ReadImage(sitkUtils.GetSlicerITKReadWriteAddress(input_node_name))
#                     fileName = item + self.file_extension_docker
#                     inputDict[item] = fileName
#                     sitk.WriteImage(img, str(os.path.join(TMP_PATH, fileName)))
#                     #except Exception as e:
#                     #    print(e.message)
#                 elif iodict[item]["type"] == "configuration":
#                     with open(USER_CONFIG, 'w') as configfile:
#                         self.user_configuration.write(configfile)
#                     #inputDict[item] = configfile
#             elif iodict[item]["iotype"] == "output":
#                 if iodict[item]["type"] == "volume":
#                       outputDict[item] = item
#                       nodes = slicer.util.getNodes(outputDict[item])
#                       if len(nodes) == 0:
#                           node = slicer.vtkMRMLLabelMapVolumeNode()
#                           node.SetName(outputDict[item])
#                           slicer.mrmlScene.AddNode(node)
#                           node.CreateDefaultDisplayNodes()
#                           imageData = vtk.vtkImageData()
#                           imageData.SetDimensions((150, 150, 150))
#                           imageData.AllocateScalars(vtk.VTK_SHORT, 1)
#                           node.SetAndObserveImageData(imageData)
#                           outputs[item] = node
#                 elif iodict[item]["type"] == "point_vec":
#                     outputDict[item] = item + '.fcsv'
#                 else:
#                     paramDict[item] = str(params[item])
#             elif iodict[item]["iotype"] == "parameter":
#                 paramDict[item] = str(params[item])
#
#         if not dataPath:
#             dataPath = '/home/deepsintef/resources'
#
#         print('docker run command:')
#         cmd = list()
#         cmd.append(self.dockerPath)
#         cmd.extend(('run', '-t', '-v'))
#         # if self.use_gpu:
#         #     cmd.append(' --runtime=nvidia ')
#         #cmd.append(TMP_PATH + ':' + dataPath)
#         cmd.append(RESOURCES_PATH + ':' + dataPath)
#         cmd.append(dockerName)
#         if not self.use_gpu:
#             cmd.append('--' + 'Task')
#             cmd.append('segmentation')
#         else:
#             cmd.append('--' + 'Task')
#             cmd.append('database')
#         arguments = []
#         for key in inputDict.keys():
#             arguments.append(key + ' ' + dataPath + '/data/' + inputDict[key])
#         # for key in outputDict.keys():
#         #     arguments.append(key + ' ' + dataPath + '/' + outputDict[key])
#         arguments.append('OutputPrefix' + ' ' + dataPath + '/data/' + 'DeepSintefOutput')
#         # arguments.append(dataPath + '/' + inputDict['InputVolume'])
#         # arguments.append(dataPath + '/' + outputDict['OutputLabel'])
#         if modelName:
#             arguments.append('ModelName ' + modelName)
#         for key in paramDict.keys():
#             if iodict[key]["type"] == "bool":
#                 if paramDict[key]:
#                     cmd.append(key)
#             else:
#                 arguments.append(key + ' ' + paramDict[key])
#         # print('-'*100)
#         cmd.append('--' + 'Arguments')
#         cmd.append(','.join(arguments))
#         print(cmd)
#
#         # TODO: add a line to check wether the docker image is present or not. If not ask user to download it.
#         # try:
#         p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
#         progress = 0
#         # print('executing')
#         while True:
#             progress += 0.15
#             slicer.app.processEvents()
#             self.cmdCheckAbort(p)
#             if widgetPresent:
#                 self.cmdProgressEvent(progress)
#             line = p.stdout.readline()
#             if not line:
#                     break
#             print(line)
#
#     def thread_doit(self, modelParameters):
#         iodict = modelParameters.iodict
#         inputs = modelParameters.inputs
#         params = modelParameters.params
#         outputs = modelParameters.outputs
#         dockerName = modelParameters.dockerImageName
#         modelName = modelParameters.modelName
#         dataPath = modelParameters.dataPath
#         #try:
#         self.main_queue_start()
#         self.executeDocker(dockerName, modelName, dataPath, iodict, inputs, outputs, params)
#         if not self.abort:
#             self.updateOutput(iodict, outputs)
#             self.main_queue_stop()
#             self.cmdEndEvent()
#
#         '''
#         except Exception as e:
#             msg = e.message
#             qt.QMessageBox.critical(slicer.util.mainWindow(), "Exception during execution of ", msg)
#             slicer.modules.DeepSintefWidget.applyButton.enabled = True
#             slicer.modules.DeepSintefWidget.progress.hide = True
#             self.abort = True
#             self.yieldPythonGIL()
#         '''
#     def main_queue_start(self):
#         """Begins monitoring of main_queue for callables"""
#         self.main_queue_running = True
#         slicer.modules.DeepSintefWidget.onLogicRunStart()
#         qt.QTimer.singleShot(0, self.main_queue_process)
#
#     def main_queue_stop(self):
#         """End monitoring of main_queue for callables"""
#         self.main_queue_running = False
#         if self.thread.is_alive():
#             self.thread.join()
#         slicer.modules.DeepSintefWidget.onLogicRunStop()
#
#     def main_queue_process(self):
#         """processes the main_queue of callables"""
#         try:
#             while not self.main_queue.empty():
#                 f = self.main_queue.get_nowait()
#                 if callable(f):
#                     f()
#
#             if self.main_queue_running:
#                 # Yield the GIL to allow other thread to do some python work.
#                 # This is needed since pyQt doesn't yield the python GIL
#                 self.yieldPythonGIL(.01)
#                 qt.QTimer.singleShot(0, self.main_queue_process)
#
#         except Exception as e:
#             import sys
#             sys.stderr.write("ModelLogic error in main_queue: \"{0}\"".format(e))
#
#             # if there was an error try to resume
#             if not self.main_queue.empty() or self.main_queue_running:
#                 qt.QTimer.singleShot(0, self.main_queue_process)
#
#     def updateOutput(self, iodict, outputs):
#         # print('updateOutput method')
#         output_volume_files = dict()
#         output_fiduciallist_files = dict()
#         self.output_raw_values = dict()
#         created_files = []
#         for _, _, files in os.walk(TMP_PATH):
#             for f, file in enumerate(files):
#                 if 'Output' in file:
#                     created_files.append(file)
#             break
#
#         nb_class = 0
#         for item in iodict:
#             if iodict[item]["iotype"] == "output":
#                 if iodict[item]["type"] == "volume":
#                     #fileName = str(os.path.join(TMP_PATH, created_files[nb_class]))
#                     fileName = str(os.path.join(TMP_PATH, created_files[[item in x for x in created_files].index(True)]))
#                     output_volume_files[item] = fileName
#                     nb_class = nb_class + 1
#                 if iodict[item]["type"] == "point_vec":
#                     fileName = str(os.path.join(TMP_PATH, item + '.fcsv'))
#                     output_fiduciallist_files[item] = fileName
#
#         self.current_threshold_class_index = 0
#         self.current_class_thresholds = [0.55] * nb_class
#
#         for output_volume in output_volume_files.keys():
#             result = sitk.ReadImage(output_volume_files[output_volume])
#             #print(result.GetPixelIDTypeAsString())
#             self.output_raw_values[output_volume] = deepcopy(sitk.GetArrayFromImage(result))
#             output_node = outputs[output_volume]
#             output_node_name = output_node.GetName()
#             nodeWriteAddress = sitkUtils.GetSlicerITKReadWriteAddress(output_node_name)
#             self.display_port = nodeWriteAddress
#             sitk.WriteImage(result, nodeWriteAddress)
#             applicationLogic = slicer.app.applicationLogic()
#             selectionNode = applicationLogic.GetSelectionNode()
#
#             outputLabelMap = True
#             if outputLabelMap:
#                 selectionNode.SetReferenceActiveLabelVolumeID(output_node.GetID())
#             else:
#                 selectionNode.SetReferenceActiveVolumeID(output_node.GetID())
#
#             applicationLogic.PropagateVolumeSelection(0)
#             applicationLogic.FitSliceToAll()
#         for fiduciallist in output_fiduciallist_files.keys():
#             # information about loading markups: https://www.slicer.org/wiki/Documentation/Nightly/Modules/Markups
#             output_node = outputs[fiduciallist]
#             _, node = slicer.util.loadMarkupsFiducialList(output_fiduciallist_files[fiduciallist], True)
#             output_node.Copy(node)
#             scene = slicer.mrmlScene
#             # todo: currently due to a bug in markups module removing the node will create some unexpected behaviors
#             # reported bug reference: https://issues.slicer.org/view.php?id=4414
#             # scene.RemoveNode(node)
#
#     def run(self, modelParamters):
#         """
#         Run the actual algorithm
#         """
#         if self.thread.is_alive():
#             import sys
#             sys.stderr.write("ModelLogic is already executing!")
#             return
#         self.abort = False
#         self.thread = threading.Thread(target=self.thread_doit(modelParameters=modelParamters))
#
#     def locate(self, modelParameters):
#         """
#         Search the local .deepsintef folder to list all models that can be used.
#         :param modelParameters:
#         :return:
#         """
#         iodict = modelParameters.iodict
#         inputs = modelParameters.inputs
#         params = modelParameters.params
#         outputs = modelParameters.outputs
#         dockerName = modelParameters.dockerImageName
#         modelName = modelParameters.modelName
#         dataPath = modelParameters.dataPath
#
#         try:
#             assert self.checkDockerDaemon(), "Docker Daemon is not running"
#         except Exception as e:
#             print(e.message)
#             self.abort = True
#
#         if not dataPath:
#             #dataPath = '/home/DeepSintef/data'
#             dataPath = '/home/deepsintef/resources'
#
#         print('docker run command:')
#         cmd = list()
#         cmd.append(self.dockerPath)
#         cmd.extend(('run', '-t', '-v'))
#         #cmd.append(TMP_PATH + ':' + dataPath)
#         cmd.append(RESOURCES_PATH + ':' + dataPath)
#         cmd.append(dockerName)
#         cmd.append('--' + 'Task')
#         cmd.append('parsing')
#         print(cmd)
#
#         # TODO: add a line to check wether the docker image is present or not. If not ask user to download it.
#         # try:
#         p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
#         out, err = p.communicate()
#         print(out)
#
#         #slicer.modules.DeepSintefWidget.populateSubModelFromDocker(out)
#         slicer.modules.DeepSintefWidget.populate_models_list_from_docker(out)
#
#         # progress = 0
#         # # print('executing')
#         # while True:
#         #     progress += 0.15
#         #     slicer.app.processEvents()
#         #     self.cmdCheckAbort(p)
#         #     line = p.stdout.readline()
#         #     if not line:
#         #         break
#         #     print(line)
#         #    slicer.modules.DeepSintefWidget.populateSubModelFromDocker(line)