import os
from os.path import expanduser
import shutil
import configparser


class SharedResources:
    """
    Singleton class to have access from anywhere in the code at the various local paths where the data, or code are
    located.
    """
    __instance = None

    @staticmethod
    def getInstance():
        """ Static access method. """
        if SharedResources.__instance == None:
            SharedResources()
        return SharedResources.__instance

    def __init__(self):
        """ Virtually private constructor. """
        if SharedResources.__instance != None:
            raise Exception("This class is a singleton!")
        else:
            SharedResources.__instance = self

    def set_environment(self):
        self.home_path = expanduser("~")
        current_folder = os.path.dirname(os.path.realpath(__file__))
        self.icon_dir = os.path.join(current_folder, '../../', '/Resources/Icons/')

        self.deepsintef_dir = os.path.join(self.home_path, '.deepsintef')
        if not os.path.isdir(self.deepsintef_dir):
            os.mkdir(self.deepsintef_dir)

        self.json_cloud_dir = os.path.join(self.deepsintef_dir, 'json', 'cloud')
        if os.path.isdir(self.json_cloud_dir):
            shutil.rmtree(self.json_cloud_dir)
        os.makedirs(self.json_cloud_dir)

        self.json_local_dir = os.path.join(self.deepsintef_dir, 'json', 'local')
        if not os.path.isdir(self.json_local_dir):
            os.makedirs(self.json_local_dir)

        self.resources_path = os.path.join(self.deepsintef_dir, 'resources')

        self.data_path = os.path.join(self.resources_path, 'data')
        if os.path.isdir(self.data_path):
            shutil.rmtree(self.data_path)
        os.makedirs(self.data_path)

        self.user_config_filename = os.path.join(self.data_path, 'runtime_config.ini')
        self.diagnosis_config_filename = os.path.join(self.data_path, 'diagnosis_config.ini')

        self.output_path = os.path.join(self.resources_path, 'output')
        if os.path.isdir(self.output_path):
            shutil.rmtree(self.output_path)
        os.makedirs(self.output_path)

        self.docker_path = None
        self.__set_runtime_parameters()

    def __set_runtime_parameters(self):
        # Set of variables sent to the docker images as runtime config, manually chosen by the user.
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