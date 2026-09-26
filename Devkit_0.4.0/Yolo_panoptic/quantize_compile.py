#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SiMa.ai Model Quantization and Compilation Utility
This script provides a command-line interface to quantize and compile machine learning models
for SiMa.ai MLSoC and Modalix hardware.

Key Features:
- Supports ONNX, TFLite, Keras, and PyTorch formats.
- Automated ONNX simplification and shape inference.
- Flexible calibration data support (Dummy or Real images).
- Comprehensive quantization error analysis.
- Hardware-specific target optimizations.
"""

import argparse
import json
import logging
import os
import sys
import numpy as np
from PIL import Image
import torch
import onnx
from onnxsim import simplify
import dataclasses

# SiMa Model Compiler Imports
from afe.apis.defines import (
    default_quantization, quantization_scheme,
    RequantizationMode, CalibrationMethod, gen2_target,gen1_target, bfloat16_scheme,
    TensorTessellateParameters, TensorDRAMLayout, InputName
)
from afe.load.importers.general_importer import ImporterParams, ModelFormat
from afe.ir.tensor_type import ScalarType
from afe.apis.loaded_net import load_model
from afe.apis.error_handling_variables import enable_verbose_error_messages
from afe.apis.release_v1 import get_model_sdk_version
from afe.ir.node import node_is_tuple
from sima_utils.data.data_generator import DataGenerator
from afe.core.utils import convert_data_generator_to_iterable

# Custom Logger to bypass SDK interference
class PrintLogger:
    def info(self, msg):
        print(f"[INFO] {msg}")
        sys.stdout.flush()
    def warning(self, msg):
        print(f"[WARN] {msg}")
        sys.stdout.flush()
    def error(self, msg):
        print(f"[ERROR] {msg}")
        sys.stdout.flush()

logger = PrintLogger()

# Constants
_ONNX_IR_VERSION = 8
_ONNX_OPSET_VERSION = 17
DIVIDER = "-" * 60


def build_quantization_manifest(*, bf16_activations, bf16_weights, device):
    """Describe the effective precision selected by the quantization config."""
    effective_bf16_activations = bf16_activations or bf16_weights
    return {
        "activation_precision": "bfloat16" if effective_bf16_activations else "int8",
        "weight_precision": "bfloat16" if bf16_weights else "int8",
        "device": device,
    }


class ModelProcessor:
    def __init__(self, args):
        self.args = args
        
        # Auto-detect input names if not provided (ONNX only)
        if not self.args.input_names:
            if self.args.model_format == 'onnx':
                try:
                    logger.info(f"Input names not provided. Auto-detecting from {self.args.model_path}...")
                    model_proto = onnx.load(self.args.model_path)
                    self.args.input_names = [node.name for node in model_proto.graph.input]
                    logger.info(f"Detected inputs: {self.args.input_names}")
                except Exception as e:
                    logger.error(f"Failed to auto-detect input names: {e}")
                    raise ValueError("Could not detect input names. Please provide --input_names manually.")
            else:
                 raise ValueError(f"Auto-detection of input names is only supported for ONNX. Please provide --input_names for {self.args.model_format}.")

        if not self.args.input_shapes:
            if self.args.model_format == 'onnx':
                try:
                    logger.info(f"Input shapes not provided. Auto-detecting from {self.args.model_path}...")
                    if 'model_proto' not in locals():
                         model_proto = onnx.load(self.args.model_path)
                    
                    detected_shapes = []
                    for node in model_proto.graph.input:
                         # Get tensor shape
                         dims = [d.dim_value for d in node.type.tensor_type.shape.dim]
                         # Check for dynamic dimensions (0 or negative often used for dynamic)
                         if any(d <= 0 for d in dims):
                             raise ValueError(f"Input '{node.name}' has dynamic shape {dims}. Cannot auto-detect.")
                         detected_shapes.append(tuple(dims))
                    
                    self.input_shapes = detected_shapes
                    logger.info(f"Detected input shapes: {self.input_shapes}")
                    
                    # Also populate input_names if we just loaded the proto and they were missing
                    if not self.args.input_names:
                         self.args.input_names = [node.name for node in model_proto.graph.input]
                         logger.info(f"Detected inputs: {self.args.input_names}")

                except Exception as e:
                    logger.error(f"Failed to auto-detect input shapes: {e}")
                    raise ValueError("Could not detect static input shapes (or dynamic shapes found). Please provide --input_shapes manually.")
            else:
                 raise ValueError(f"Auto-detection of input shapes is only supported for ONNX. Please provide --input_shapes manually.")
        else:
            self.input_shapes = [tuple(map(int, s.split(','))) for s in args.input_shapes]
        self.output_path = os.path.join(args.build_dir, os.path.splitext(os.path.basename(args.model_path))[0])
        os.makedirs(self.output_path, exist_ok=True)
        
        enable_verbose_error_messages()
        logger.info(DIVIDER)
        logger.info(f"Model Compiler Version: {get_model_sdk_version()}")
        logger.info(f"Python Version: {sys.version.split()[0]}")
        logger.info(f"Output Directory: {self.output_path}")
        logger.info(DIVIDER)

    @staticmethod
    def normalize_tensor(tensor, mean, std, layout='NCHW'):
        """Applies mean/std normalization to an image tensor scaled to [0, 1]."""
        tensor = torch.tensor(tensor).float() / 255.0
        
        ch_idx = 1 if layout == 'NCHW' else 3
        if len(tensor.shape) >= 4:
            channels = tensor.shape[ch_idx]
            
            # Adjust mean/std if they don't match the channel count
            # Use defaults (0 mean, 1 std) if dimensions mismatch
            if mean is not None and len(mean) != channels:
                mean = [0.0] * channels
            if std is not None and len(std) != channels:
                std = [1.0] * channels
                
            if mean is not None and std is not None:
                view_shape = [1] * 4
                view_shape[ch_idx] = channels
                mean_t = torch.tensor(mean).view(*view_shape)
                std_t = torch.tensor(std).view(*view_shape)
                tensor = (tensor - mean_t) / std_t
        return tensor

    def preprocess_image(self, image_path, input_shape=None):
        """Loads and prepares a single image for calibration."""
        target_shape = input_shape if input_shape is not None else self.input_shapes[0]
        if self.args.model_layout == 'NCHW':
            h, w = target_shape[2], target_shape[3]
        else:
            h, w = target_shape[1], target_shape[2]

        # Match the deployed CVU image preprocessor. Calibration with Pillow's
        # default bicubic resize shifts activation ranges from the runtime's
        # bilinear input distribution.
        image = Image.open(image_path).convert("RGB").resize(
            (w, h), resample=Image.Resampling.BILINEAR
        )
        image_np = np.array(image)
        # Permute to NCHW for normalization logic, then we'll flip back to NHWC if needed
        image_t = torch.tensor(image_np).permute(2, 0, 1).unsqueeze(0)
        return self.normalize_tensor(image_t, self.args.mean, self.args.std, 'NCHW')

    def prepare_onnx(self):
        """Simplifies ONNX model and enforces required versions/shapes."""
        logger.info(f"Preparing ONNX model: {self.args.model_path}")
        simplified_path = f"{os.path.splitext(self.args.model_path)[0]}_prepared.onnx"
        
        try:
            # Build input shapes dict for all inputs
            input_shapes_dict = {
                name: list(shape) 
                for name, shape in zip(self.args.input_names, self.input_shapes)
            }
            logger.info(f"Simplifying with fixed shapes: {input_shapes_dict}")
            
            # Simplify with fixed input shapes to eliminate dynamic Shape/Gather ops
            model_proto, check = simplify(
                self.args.model_path,
                overwrite_input_shapes=input_shapes_dict,
                dynamic_input_shape=False
            )
            if not check:
                raise ValueError("ONNX simplification validation failed")
            
            # Reset and fix input shapes
            for info in list(model_proto.graph.value_info):
                model_proto.graph.value_info.remove(info)
            
            # Update all inputs
            for input_tensor in model_proto.graph.input:
                if input_tensor.name in input_shapes_dict:
                    target_shape = input_shapes_dict[input_tensor.name]
                    input_tensor.type.tensor_type.shape.ClearField("dim")
                    for dim_size in target_shape:
                        dim = input_tensor.type.tensor_type.shape.dim.add()
                        dim.dim_value = dim_size
            
            model_proto = onnx.shape_inference.infer_shapes(model_proto)
            model_proto.ir_version = _ONNX_IR_VERSION
            # model_proto = onnx.version_converter.convert_version(model_proto, _ONNX_OPSET_VERSION)
            
            onnx.save(model_proto, simplified_path)
            logger.info(f"Prepared model saved to: {simplified_path}")
            return simplified_path
        except Exception as e:
            logger.error(f"Failed to prepare ONNX: {e}")
            return self.args.model_path

    def get_calibration_data(self):
        """Generates dummy or real calibration data based on user input."""
        if not self.args.real_data:
            logger.info("Generating dummy calibration data...")
            data_dict = {}
            for name, shape in zip(self.args.input_names, self.input_shapes):
                dummy_raw = np.random.randint(0, 256, size=shape)
                data_in = self.normalize_tensor(dummy_raw, self.args.mean, self.args.std, self.args.model_layout).cpu().numpy()
                if self.args.model_layout == 'NCHW':
                     data_in = data_in.transpose(0, 2, 3, 1) # SDK expects NHWC
                data_dict[name] = data_in
            return [data_dict]
        
        logger.info(f"Loading real calibration data from: {self.args.dataset_images}")
        image_exts = (".jpg", ".jpeg", ".png", ".bmp")
        image_paths = sorted(
            os.path.join(self.args.dataset_images, f)
            for f in os.listdir(self.args.dataset_images)
            if f.lower().endswith(image_exts)
        )[:self.args.num_calib_samples]
        
        if not image_paths:
            raise FileNotFoundError(f"No valid images found in {self.args.dataset_images}")

        # Feed the same image set to all model inputs.
        # If input shapes differ, each input receives the same source images resized to its own shape.
        inputs_dict = {}
        for input_name, input_shape in zip(self.args.input_names, self.input_shapes):
            calib_images = torch.stack(
                [self.preprocess_image(p, input_shape=input_shape) for p in image_paths]
            ).squeeze(1)
            # SDK expects NHWC input shape
            inputs_dict[InputName(input_name)] = calib_images.cpu().numpy().transpose(0, 2, 3, 1)
        return convert_data_generator_to_iterable(DataGenerator(inputs_dict))

    def run(self):
        model_path = self.args.model_path
        if self.args.model_format == 'onnx' and self.args.simplify:
            model_path = self.prepare_onnx()

        # Step 1: Import and Load
        # Auto-detect output names if not provided
        output_names = self.args.output_names
        if not output_names:
            logger.info("Output names not provided. Auto-detecting from ONNX model...")
            try:
                model_proto = onnx.load(model_path)
                output_names = [node.name for node in model_proto.graph.output]
                logger.info(f"Detected outputs: {output_names}")
            except Exception as e:
                logger.error(f"Failed to auto-detect output names: {e}")
                raise ValueError("Could not detect output names. Please provide --output_names manually.")

        importer_params = ImporterParams(
            format=ModelFormat[self.args.model_format],
            file_paths=[model_path],
            input_names=self.args.input_names,
            input_shapes=self.input_shapes,
            input_types=[ScalarType.float32] * len(self.args.input_names),
            layout=self.args.model_layout.upper(),
            output_names=output_names
        )
        
        target_device = gen2_target if self.args.device == "modalix" else gen1_target
        # C2PSA's window-attention MatMul/Softmax reshapes fold the batch axis into the
        # head-count axis using the literal traced batch value (same issue as SwinIR's
        # window partitioning), so the framework's flexible-batch-size re-batch check
        # fails hard on those nodes. All models compiled by this project script are
        # fixed at batch=1; disable that check per the framework's own suggested fix.
        loaded_net = load_model(importer_params, target=target_device, flexible_batch_size=not self.args.fixed_batch)
        logger.info(f"Model successfully loaded for {self.args.device}")

        # Step 2: Quantization
        logger.info("Initializing quantization...")
        calib_data = self.get_calibration_data()
        rq_mode = RequantizationMode.sima if self.args.requant_mode == 'sima' else RequantizationMode.tflite
        calib_method = CalibrationMethod.from_str(self.args.calib_method)

        if self.args.bf16_activations or self.args.bf16_weights: # if weights are bf16, activations must be too
            act_scheme = bfloat16_scheme()
        else:
            act_scheme = quantization_scheme(True, False, 8)

        if self.args.bf16_weights:
            weight_scheme = bfloat16_scheme()
        else:
            weight_scheme = quantization_scheme(False, True, 8)

        quantization_manifest = build_quantization_manifest(
            bf16_activations=self.args.bf16_activations,
            bf16_weights=self.args.bf16_weights,
            device=self.args.device,
        )

        quant_config = default_quantization.with_activation_quantization(act_scheme) \
                                   .with_weight_quantization(weight_scheme) \
                                   .with_requantization_mode(rq_mode) \
                                   .with_calibration(calib_method)

        # Derive model name from ONNX file
        model_basename = os.path.splitext(os.path.basename(self.args.model_path))[0]
        
        quant_model = loaded_net.quantize(
            calibration_data=calib_data,
            quantization_config=quant_config,
            any_shape_on_mla=self.args.any_shape_on_mla,
            automatic_layout_conversion=self.args.auto_layout,
            model_name=model_basename,
            log_level=logging.INFO
        )

        if self.args.analyse_error:
            logger.info("Analyzing quantization error (this may take a while)...")
            quant_model.analyze_quantization_error(evaluation_data=calib_data, error_metric="mae", local_feed=True)

        quant_model.save(model_name=model_basename, output_directory=self.output_path)
        logger.info("Quantized model saved.")
        
        # Debug: Print internal node names for tessellate parameters
        logger.info(DIVIDER)
        logger.info("Internal graph structure (for tessellate parameters):")
        logger.info(f"  Input node names: {quant_model._net.input_node_names}")
        logger.info(f"  Output node name: {quant_model._net.output_node_name}")
        
        # Show all nodes to find placeholder names
        logger.info("  All nodes:")
        for node_name in quant_model._net.nodes.keys():
            node = quant_model._net.nodes[node_name]
            from afe.ir.node import node_is_placeholder
            if node_is_placeholder(node):
                logger.info(f"    PLACEHOLDER: {node_name}")
        logger.info(DIVIDER)

        # Step 3: Compilation
        if self.args.compile:
            logger.info(f"Compiling for {self.args.device} with batch size {self.args.batch_size}...")

            tess_params = {}
            if self.args.mla_tesselation:
                logger.info(DIVIDER)
                logger.info("MLA TESSELATION MODE ENABLED: Using internal MLA node names")
                
                # Get the MLA node to access internal names
                logger.info("DEBUG: Checking for MLA_0 node...")
                logger.info(f"DEBUG: Available nodes: {list(quant_model._net.nodes.keys())[:10]}...")
                
                assert "MLA_0" in quant_model._net.nodes, "MLA_0 node not found in compiled model"
                mla_node = quant_model._net.nodes["MLA_0"]
                
                logger.info(f"DEBUG: MLA node found!")
                logger.info(f"DEBUG: MLA node has {len(mla_node.input_names)} inputs")
                logger.info(f"DEBUG: MLA input names: {mla_node.input_names}")
                
                # # Set input tessellate parameters using MLA internal names
                input_tess_params = TensorTessellateParameters(
                    tile_shape=(0, 0, 0, 0),
                    enable_mla=True,
                    dram_layout=TensorDRAMLayout.HWC
                )
                
                for input_idx, input_name in enumerate(mla_node.input_names):
                    logger.info(f"  Input {input_idx}: '{input_name}' -> MLA Direct (HWC)")
                    tess_params[input_name] = dataclasses.replace(
                        input_tess_params
                    )
                
                # Set output tessellate parameters using MLA internal names
                output_tess_params = TensorTessellateParameters(
                    tile_shape=(0, 0, 0, 0),
                    enable_mla=True,
                    dram_layout=TensorDRAMLayout.HWC16
                )
                
                logger.info(f"DEBUG: MLA output node name: {mla_node.ir.output_node_name}")
                output_node = mla_node.ir.nodes[mla_node.ir.output_node_name]
                logger.info(f"DEBUG: Output node is tuple: {node_is_tuple(output_node)}")
                
                out_names = output_node.input_node_names if node_is_tuple(output_node) else [output_node.name]
                logger.info(f"DEBUG: Output names: {out_names}")
                
                for output_idx, output_name in enumerate(out_names):
                    output_key = f"{output_name}_output"
                    logger.info(f"  Output {output_idx}: '{output_key}' -> MLA Direct (HWC16)")
                    tess_params[output_key] = dataclasses.replace(
                        output_tess_params
                    )
                
                logger.info(DIVIDER)
                logger.info(f"DEBUG: Final tessellate_parameters keys: {list(tess_params.keys())}")
                logger.info(DIVIDER)
            
            quant_model.compile(
                output_path=self.output_path,
                batch_size=self.args.batch_size,
                log_level=logging.INFO,
                tessellate_parameters=tess_params if tess_params else None
            )
            manifest_path = os.path.join(self.output_path, "quantization_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as manifest_file:
                json.dump(quantization_manifest, manifest_file, indent=2, sort_keys=True)
                manifest_file.write("\n")
            logger.info(f"Quantization manifest saved to: {manifest_path}")
            logger.info("Compilation complete.")

        # Step 4: Verification
        if self.args.verify:
            logger.info("Running execution comparison verification...")
            test_inputs = {}
            for name, shape in zip(self.args.input_names, self.input_shapes):
                # Always test with a single sample
                test_shape = (1, *shape[1:])
                raw_test = np.random.randint(0, 256, size=test_shape)
                test_data = self.normalize_tensor(raw_test, self.args.mean, self.args.std, self.args.model_layout).cpu().numpy()
                if self.args.model_layout == 'NCHW':
                     # Ensure channel-last for SDK execution
                     test_data = test_data.transpose(0, 2, 3, 1)
                test_inputs[InputName(name)] = test_data
                logger.info(f"Prepared test input '{name}' with shape {test_data.shape}")

            use_jax = (self.args.executor == "jax")
            logger.info(f"Executing quantized model (backend={'jax' if use_jax else 'normal'})...")
            quant_out = quant_model.execute(inputs=test_inputs, use_jax=use_jax)
            
            logger.info("Executing floating point model...")
            fp_out = loaded_net.execute(inputs=test_inputs)

            if not fp_out or not quant_out:
                logger.warning("Execution returned no outputs!")

            # Check output counts
            fp_out = list(fp_out)
            quant_out = list(quant_out)
            logger.info(f"Got {len(fp_out)} outputs from FP model and {len(quant_out)} from Quant model.")

            for i, (f_out, q_out) in enumerate(zip(fp_out, quant_out)):
                diff = np.abs(f_out - q_out)
                out_name = output_names[i] if i < len(output_names) else f"index {i}"
                logger.info(f"Output {i} ({out_name}):")
                logger.info(f"  Max Absolute Diff: {np.max(diff):.6f}")
                logger.info(f"  Mean Absolute Diff: {np.mean(diff):.6f}")

def main():
    parser = argparse.ArgumentParser(description="SiMa.ai Comprehensive Model Quantization & Compilation")
    
    # Model Metadata
    parser.add_argument("--model_path", required=True, help="Path to input model")
    parser.add_argument("--model_format", default="onnx", choices=["onnx", "tflite", "keras", "pytorch"], help="Source format")
    parser.add_argument("--model_layout", default="NCHW", choices=["NCHW", "NHWC"], help="Input tensor layout")
    parser.add_argument("--input_names", nargs="+", required=False, help="Input node names (optional, auto-detected if omitted)")
    parser.add_argument("--input_shapes", nargs="+", required=False, help="Input shapes (e.g. 1,3,224,224) (optional, detected if static)")
    parser.add_argument("--output_names", nargs="+", required=False, help="Output node names (optional, auto-detected if omitted)")
    
    # Workflow Flags
    parser.add_argument(
        "--device",
        default="modalix",
        choices=["modalix", "mlsoc"],
        help="Target hardware (mlsoc=MLSoC, modalix=Modalix)",
    )
    parser.add_argument("--build_dir", default="./build", help="Target directory for artifacts")
    parser.add_argument("--no-simplify", action="store_false", dest="simplify", help="Disable ONNX simplification")
    parser.add_argument("--fixed-batch", action="store_true", default=False,
                         help="Disable flexible_batch_size (needed for graphs with batch-folding reshapes, e.g. C2PSA/window attention)")
    parser.add_argument("--no-compile", action="store_false", dest="compile", help="Skip the compilation step")
    parser.add_argument("--analyse-error", action="store_true", help="Perform per-layer error analysis")
    parser.add_argument("--verify", action="store_true", default=False, help="Run bit-accuracy comparison")
    
    # Quantization Settings
    parser.add_argument("--bf16-weights", action="store_true", help="Use BFloat16 for weights")
    parser.add_argument("--bf16-activations", action="store_true", help="Use BFloat16 for activations")
    parser.add_argument("--calib_method", default="mse", help="Calibration method (mse, entropy, etc.)")
    parser.add_argument("--requant_mode", default="sima", choices=["sima", "tflite"], help="Requantization mode")
    
    # Calibration Data
    parser.add_argument("--real_data", action="store_true", help="Use images for calibration")
    parser.add_argument("--dataset_images", default="./calib_images", help="Path to calibration images")
    parser.add_argument("--num_calib_samples", type=int, default=50, help="Max images for calibration")
    
    # Normalization
    parser.add_argument("--mean", type=float, nargs=3, default=[0, 0, 0], help="Image mean (RGB)")
    parser.add_argument("--std", type=float, nargs=3, default=[1, 1, 1], help="Image standard deviation (RGB)")

    # Advanced SDK Tweaks
    parser.add_argument("--batch_size", type=int, default=1, help="Compilation batch size")
    parser.add_argument("--executor", default="jax", choices=["jax", "normal"], help="Backend for verification")
    parser.add_argument("--any_shape_on_mla", action="store_true", default=False, help="Allow non-4D ops on MLA")
    parser.add_argument("--auto_layout", action="store_true", default=False, help="Enable automatic graph surgery")

    # Advanced Tessellation
    parser.add_argument("--mla-tesselation", action="store_true", help="Force ALL inputs (HWC) and outputs (HWC16) to direct MLA mode, bypassing EV74")

    args = parser.parse_args()
    processor = ModelProcessor(args)
    processor.run()

if __name__ == "__main__":
    main()
