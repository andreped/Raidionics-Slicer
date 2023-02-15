import configparser
import os
import json
from src.utils.resources import SharedResources


def generate_backend_config(input_folder, parameters, logic_target_space, model_name):
    rads_config = configparser.ConfigParser()
    rads_config.add_section('Default')
    rads_config.set('Default', 'task', logic_target_space)
    rads_config.set('Default', 'caller', '')
    rads_config.add_section('System')
    rads_config.set('System', 'gpu_id', "-1")  # Always running on CPU
    rads_config.set('System', 'input_folder', '/home/ubuntu/resources/data')
    rads_config.set('System', 'output_folder', '/home/ubuntu/resources/output')
    rads_config.set('System', 'model_folder', '/home/ubuntu/resources/models')
    rads_config.set('System', 'pipeline_filename', '/home/ubuntu/resources/models/' + model_name + '/pipeline.json')
    rads_config.add_section('Runtime')
    rads_config.set('Runtime', 'reconstruction_method',
                    SharedResources.getInstance().user_configuration['Predictions']['reconstruction_method'])
    rads_config.set('Runtime', 'reconstruction_order',
                    SharedResources.getInstance().user_configuration['Predictions']['reconstruction_order'])
    # rads_config.add_section('Neuro')
    # if SoftwareConfigResources.getInstance().user_preferences.compute_cortical_structures:
    #     rads_config.set('Neuro', 'cortical_features', 'MNI, Schaefer7, Schaefer17, Harvard-Oxford')
    # if SoftwareConfigResources.getInstance().user_preferences.compute_subcortical_structures:
    #     rads_config.set('Neuro', 'subcortical_features', 'BCB')
    rads_config_filename = os.path.join(input_folder, 'rads_config.ini')
    with open(rads_config_filename, 'w') as outfile:
        rads_config.write(outfile)