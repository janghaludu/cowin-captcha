from chalice import Chalice
from collections import Counter
from hashlib import sha256
from cairosvg import svg2png
import re
import cv2
import numpy as np
from mahotas.features import zernike_moments as zernikes
from random import choice
from warnings import filterwarnings
import pickle
import json
import string
import os
from urllib.request import urlopen

filterwarnings("ignore")

    
app = Chalice(app_name='cowidcapture')

stragent = 'Mozilla/5.0 (Linux; Android 8.0.0; Pixel 2 XL Build/OPD1.170816.004) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.72 Mobile Safari/537.36'

with open("chalicelib/zerns", "rb") as f:
    zerns = pickle.load(f)
       
with open("chalicelib/indexTags.json", "r") as f:
    indexTags = json.load(f)
    
    
# @app.schedule('rate(1 minute)')
# def warmer():
#     with urlopen(os.getenv("PRODUCTION_URL")) as conn:
#         print("Warming...")
        

# @app.route("/warm", methods=["GET"])
# def temperature():
#     return {"p": {"a" : {"n" : {"i" : {"n" : "i"}}}}}    

    
@app.route('/', methods=["POST"])
def captchax():
    request = app.current_request
    svgData = request.json_body
    randStr = ''.join(choice(string.ascii_uppercase + \
        string.digits) for _ in range(10))
    svgtext = svgData['captcha']
    svg2png(bytestring=re.sub('(<path d=)(.*?)(fill=\"none\"/>)',
                              '',svgtext),  
            write_to=f"/tmp/{randStr}.png")
     
    path = "/tmp/" + randStr + ".png"
    targetchars = segmentedCharacters(path)
    for el in targetchars:
        el[el > 0] = 1
        
    charZerns = getZerns(targetchars)

    result = []
    for i,cz in enumerate(charZerns):
        clozest = closest(cz)
        charsu = Counter([indexTags.get(c, indexTags.get(str(c))) for c in clozest]).most_common()[0][0]
        result.append(charsu)
        
        
    os.remove(f"/tmp/{randStr}.png")
    return ''.join(result)

def transformImage(path):
    im = cv2.imread(path)
    imgray = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    ret, thresh = cv2.threshold(imgray, 1, 255, 0)
    img = cv2.threshold(imgray, 1, 255, cv2.THRESH_BINARY)[1]
    kernel = np.ones((1,1),np.uint8)
    img  = cv2.dilate(img,kernel,iterations = 2)
    floodedImage = img.copy()
    floodedImage = floodedImage.astype("uint8")

    h, w = img.shape[:2]
    mask = np.zeros((h+2, w+2), np.uint8)
    img = cv2.bitwise_not(cv2.floodFill(floodedImage, mask, (0,0), 255)[1])

    imgCopy = np.empty_like(img)
    np.copyto(imgCopy,img)
    height, width = img.shape

    activeColumnPixelsHistogram = []
    activePixels = 0

    for col in range(width):
        whitePixels = 0
        for row in range(height):
            if img[row][col] == 255:
                whitePixels += 1
                activePixels += 1
        activeColumnPixelsHistogram.append(whitePixels)



    samples = np.zeros(shape=(activePixels,2))
    counter = 0
    for row in range(height):
        for col in range(width):
            if img[row][col] == 255:
                samples[counter] = np.array([row,col])
                counter += 1



    z = np.float32(samples)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    flags = cv2.KMEANS_RANDOM_CENTERS
    K = 5
    compactness,labels,centers = cv2.kmeans(z,K,None,criteria,10,flags)

    colors = [1,2,3,4,5]
    copyImage = np.empty_like(img)
    np.copyto(copyImage,img)



    for i in range(len(samples)):
        row,col = int(samples[i][0]),int(samples[i][1])
        clusterColor = colors[labels[i][0]]
        copyImage[row][col] = clusterColor


    return copyImage


def segmentedCharacters(path):
    chars = []
    copyImage = transformImage(path)
    colorsDiscovered = {}
    for i,row in enumerate(copyImage.transpose()):
        if sum(row) != 0:
            for cell in row:
                if cell != 0:
                    if cell not in colorsDiscovered:
                        colorsDiscovered[cell] = [i]
                    else:
                        colorsDiscovered[cell] += [i]

                    break


    for color, crange in colorsDiscovered.items():
        chars.append(copyImage[:, min(crange) : max(crange) + 1])
        
    return chars



def getZerns(chars):
    zerns = []
    for el in chars:
        zerns.append(zernikes(el, 10))
    return zerns
    
    
def closest(zern):
    dists = []
    M = len(zerns)
    D = np.empty(M, dtype=np.float)

    for i,el in enumerate(zerns):
        D[i] = np.linalg.norm(el-zern)
        
    return D.argsort()[:100]