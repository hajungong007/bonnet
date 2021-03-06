#!/usr/bin/python3

# Copyright 2017 Andres Milioto. All Rights Reserved.
#
#  This file is part of Bonnet.
#
#  Bonnet is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Bonnet is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Bonnet. If not, see <http://www.gnu.org/licenses/>.

'''
  Use the trained frozen pb on an input video.
'''
import os
import argparse
import yaml
import time

# image plot stuff
import cv2
import skvideo.io as skio
import numpy as np

import dataset.aux_scripts.util as util

# tensorflow stuff
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # shut up TF!
import tensorflow as tf
import signal


def predict_mask(cvim, frame, sess, input, output, FLAGS, DATA):
  # predict mask from image
  cvim = cv2.cvtColor(cvim, cv2.COLOR_RGB2BGR)
  start = time.time()
  mask = sess.run(output, feed_dict={input: [cvim]})
  elapsed = time.time() - start
  print("Prediction for frame ", frame, ". Elapsed: ", elapsed, "s")

  # change to color
  color_mask = util.prediction_to_color(
      mask[0, :, :], DATA["label_remap"], DATA["color_map"])
  im, transparent_mask = util.transparency(cvim, color_mask)
  all_img = np.concatenate((im, transparent_mask), axis=1)
  w, h, _ = all_img.shape
  watermark = "Time: {:.3f}s, FPS: {:.3f}img/s.".format(elapsed, 1 / elapsed)
  cv2.putText(all_img, watermark,
              org=(10, w - 10),
              fontFace=cv2.FONT_HERSHEY_SIMPLEX,
              fontScale=0.75,
              color=(255, 255, 255),
              thickness=2,
              lineType=cv2.LINE_AA)

  # write to disk
  cv2.imwrite(FLAGS.log + "/mask_" + frame + ".jpg", color_mask)
  cv2.imwrite(FLAGS.log + "/full_" + frame + ".jpg", all_img)

  # show me the image
  cv2.imshow("video", all_img.astype(np.uint8))
  ch = cv2.waitKey(1)

  return ch


if __name__ == '__main__':
  parser = argparse.ArgumentParser("./cnn_video_pb.py")
  parser.add_argument(
      '--video', '-v',
      type=str,
      required=False,
      default="",
      help='Video to infer.',
  )
  parser.add_argument(
      '--log', '-l',
      type=str,
      default='/tmp/pb_predictions/',
      help='Directory to log output of predictions. Defaults to %(default)s',
  )
  parser.add_argument(
      '--path', '-p',
      type=str,
      required=True,
      help='Directory to get the model. No default!'
  )
  model_choices = ['frozen_nchw', 'frozen_nhwc', 'optimized', 'quantized']
  parser.add_argument(
      '--model', '-m',
      type=str,
      default='frozen_nchw',
      help='Type of model (frozen or optimized). Default to %(default)s',
      choices=model_choices
  )
  parser.add_argument(
      '--verbose',
      dest='verbose',
      default=False,
      action='store_true',
      help='Verbose mode. Calculates profile. Defaults to %(default)s',
  )
  FLAGS, unparsed = parser.parse_known_args()

  # print summary of what we will do
  print("----------")
  print("INTERFACE:")
  print("Video to infer: ", FLAGS.video)
  print("Log dir: ", FLAGS.log)
  print("model path", FLAGS.path)
  print("model type", FLAGS.model)
  print("Verbose?: ", FLAGS.verbose)
  print("----------\n")

  # try to open data yaml
  try:
    print("Opening default data file data.yaml from log folder")
    f = open(FLAGS.path + '/data.yaml', 'r')
    DATA = yaml.load(f)
  except:
    print("Error opening data yaml file...")
    quit()

  # try to open net yaml
  try:
    print("Opening default net file net.yaml from log folder")
    f = open(FLAGS.path + '/net.yaml', 'r')
    NET = yaml.load(f)
  except:
    print("Error opening net yaml file...")
    quit()

  # try to open train yaml
  try:
    print("Opening default train file train.yaml from log folder")
    f = open(FLAGS.path + '/train.yaml', 'r')
    TRAIN = yaml.load(f)
  except:
    print("Error opening train yaml file...")
    quit()

  # try to open nodes yaml
  try:
    print("Opening default nodes file nodes.yaml from log folder")
    f = open(FLAGS.path + '/nodes.yaml', 'r')
    NODES = yaml.load(f)
  except:
    print("Error opening nodes yaml file...")
    quit()

  frozen_name = os.path.join(FLAGS.path, FLAGS.model + ".pb")
  if tf.gfile.Exists(frozen_name):
    print("Model file exists! Using model from %s" % (frozen_name))
  else:
    print("Model not found. Exiting...")
    quit()

  # create log folder
  try:
    if tf.gfile.Exists(FLAGS.log):
      tf.gfile.DeleteRecursively(FLAGS.log)
    tf.gfile.MakeDirs(FLAGS.log)
  except:
    print("Error creating log directory. Check permissions! Exiting...")
    quit()

  # node names
  input_node = NODES["input_node"] + ':0'
  code_node = NODES["code_node"] + ':0'
  mask_node = NODES["mask_node"] + ':0'

  with tf.Graph().as_default() as graph:
    # open graph def from frozen model
    try:
      with tf.gfile.GFile(frozen_name, "rb") as f:
        graph_def = tf.GraphDef()
        graph_def.ParseFromString(f.read())
    except:
      print("Failed to extract grapfdef. Exiting...")
      quit()

    # import the graph
    pl, code, mask = tf.import_graph_def(graph_def, return_elements=[
                                         input_node, code_node, mask_node])

    # infer from pb
    gpu_options = tf.GPUOptions(allow_growth=True, force_gpu_compatible=True)
    config = tf.ConfigProto(allow_soft_placement=True,
                            log_device_placement=False, gpu_options=gpu_options)
    config.graph_options.optimizer_options.global_jit_level = tf.OptimizerOptions.ON_2

    # start a session
    sess = tf.Session(config=config)

    # create resizeable window
    cv2.namedWindow("video", cv2.WINDOW_NORMAL)

    # open video capture
    if FLAGS.video is "":
      print("Webcam reading not implemented. Exiting")
      quit()
    else:
      inputparameters = {}
      outputparameters = {}
      reader = skio.FFmpegReader(FLAGS.video,
                                 inputdict=inputparameters,
                                 outputdict=outputparameters)

      i = 0
      for frame in reader.nextFrame():
        # predict
        ch = predict_mask(frame, str(i), sess, pl, mask, FLAGS, DATA)
        if ch == 27:
          break
        # add to frame nr.
        i += 1
      # clean up
      cv2.destroyAllWindows()
