from pathlib import Path
import argparse
import cv2
import numpy as np

parser=argparse.ArgumentParser(description='Generate an ArUco DICT_4X4_50 marker; measure the printed black outer square.')
parser.add_argument('--id',type=int,default=0,choices=range(50))
parser.add_argument('--output',type=Path)
args=parser.parse_args()
path=args.output or Path(__file__).resolve().parents[1]/f'dist/downloads/aruco-4x4-50-id{args.id}.png'
marker=cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),args.id,600)
image=np.full((760,760),255,dtype=np.uint8);image[80:680,80:680]=marker
path.parent.mkdir(parents=True,exist_ok=True)
if not cv2.imwrite(str(path),image):
    raise IOError('Could not write marker.')
print(path)
