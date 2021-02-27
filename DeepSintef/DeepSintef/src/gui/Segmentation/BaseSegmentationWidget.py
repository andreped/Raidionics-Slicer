from __main__ import qt, ctk, slicer, vtk
from glob import glob
import os
import json
from collections import OrderedDict
import subprocess

from src.utils.resources import SharedResources
from src.DeepSintefLogic import DeepSintefLogic
from src.gui.Segmentation.ModelsInterfaceWidget import *
from src.gui.Segmentation.ModelsExecutionWidget import *

# class BaseSegmentationWidget():
#     """
#     Main GUI object, similar to a QMainWindow, where all widgets and user interactions are defined.
#     """
#     def __init__(self, base_layout):
#         self.layout = base_layout
#
#     def setup(self):
#         """
#         Instantiate the plugin layout and connect widgets
#         :return:
#         """
#
#         # Reload and Test area
#         reloadCollapsibleButton = ctk.ctkCollapsibleButton()
#         reloadCollapsibleButton.collapsed = True
#         reloadCollapsibleButton.text = "Reload && Test"
#         reloadFormLayout = qt.QFormLayout(reloadCollapsibleButton)
#         # reload button
#         # (use this during development, but remove it when delivering
#         #  your module to users)
#         self.reloadButton = qt.QPushButton("Reload")
#         self.reloadButton.toolTip = "Reload this module."
#         self.reloadButton.name = "Freehand3DUltrasound Reload"
#         reloadFormLayout.addWidget(self.reloadButton)
#         # self.reloadButton.connect('clicked()', self.onReload)
#         # uncomment the following line for debug/development.
#         self.layout.addWidget(reloadCollapsibleButton)


class BaseSegmentationWidget(qt.QWidget):
    """
    Main GUI object, similar to a QMainWindow, where all widgets and user interactions are defined.
    """
    def __init__(self, parent=None):
        super(BaseSegmentationWidget, self).__init__(parent)
        # self.parent = parent
        self.base_layout = qt.QVBoxLayout()
        self.model_interface_widget = ModelsInterfaceWidget(parent=self)
        self.base_layout.addWidget(self.model_interface_widget)
        self.model_execution_widget = ModelsExecutionWidget(parent=self)
        self.base_layout.addWidget(self.model_execution_widget)
        # self.main_scrollarea = qt.QScrollArea(parent.widget()) # @TODO. How to add the scroll area?
        self.setLayout(self.base_layout)
        self.setup_connections()

    def reload(self):
        print('Reloading the widget segmentation!!!!!')

    def setup_connections(self):
        self.model_execution_widget.run_model_pushbutton.connect("clicked()", self.on_run_model)
        self.model_execution_widget.cancel_model_run_pushbutton.connect("clicked()", self.on_cancel_model_run)
        self.model_execution_widget.interactive_thresholding_slider.valueChanged.connect(self.on_interactive_slider_moved)
        self.model_execution_widget.interactive_optimal_thr_pushbutton.connect("clicked()", self.on_interactive_best_threshold_clicked)

    def on_run_model(self):
        DeepSintefLogic.getInstance().logic_task = 'segmentation'
        DeepSintefLogic.getInstance().run(self.model_interface_widget.model_parameters)
        self.model_execution_widget.populate_interactive_label_classes(self.model_interface_widget.model_parameters.outputs.keys())
        self.on_interactive_best_threshold_clicked()

    def on_cancel_model_run(self):
        DeepSintefLogic.getInstance().cancel_run()

    def on_logic_event_start(self):
        self.model_execution_widget.on_logic_event_start()

    def on_logic_event_end(self):
        self.model_execution_widget.on_logic_event_end()

    def on_logic_event_progress(self, progress, log):
        self.model_execution_widget.on_logic_event_progress(progress, log)

    def on_interactive_slider_moved(self, value):
        self.model_execution_widget.on_interactive_slider_moved(value, self.model_interface_widget.model_parameters)

    def on_interactive_best_threshold_clicked(self):
        self.model_execution_widget.on_interactive_best_threshold_clicked(self.model_interface_widget.model_parameters)