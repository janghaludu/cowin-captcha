################# HOW TO RUN THIS ##################

#### 1) Create a bucket on KVDB => https://kvdb.io/start
#### 2) Configure IFTTT / Shortcuts on your phone.
####    Refer to this => https://github.com/bombardier-gif/covid-vaccine-booking#ifttt-steps-in-screenshots
####    There is just one difference, you need to add 
####    Add the KVDB URL to IFFT / Shortcuts as elaborated in the link above
#### 3) Start chalice server for decoding CAPTCHA => https://github.com/janghaludu/cowin-captcha
####    Alternatively you can simply import functions from app.py in root folder and change a line here
#### 4) booker.log is where logs are stored. {phoneNumber}.json is where the state of booking is stored




# vaccer = Vaxxer(9999999999, ["505", "506"], '4s7oQvpnybgERS9ftC3duv4', 2.8, "COVISHIELD")
# while not vaccer.scheduled:
#     vaccer = Vaxxer(9999999999, ["505", "506"], '4s7oQvpnybgERS9ftC3duv4', 2.8, "COVISHIELD")
#     vaccer.run()

# 9999999999 => phoneNumber:: Integer
# ["505", "506"], => Array of District codes:: Array[::String]
# 4s9oQjpnybpGS9ftZ3duv4 => Your KVDB bucket name:: String
# 2.8 => Delay in seconds to avoid 429 errors:: Integer
# "COVISHIELD, COVAXIN, SPUTNIK" => Preferred Vaccines [String of Comma separated values]:: String
# 18 => 18 or 45 for age limit:: Integer
# 1 => 1 or 2 for Dose Number:: Integer

######################################################


from functools import wraps, partial
import time
from dataclasses import dataclass
import os
import json
import requests
import logging
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from ratelimit import limits, RateLimitException
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

def nowStamp(): return datetime.utcnow(), int((datetime.utcnow() - datetime(1970, 1, 1)).total_seconds())


logging.basicConfig(filename=f'booker-{nowStamp()[1]}.log', filemode='w', 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    level=logging.INFO)


### Load  Zernike Polynomials and Table

stragent = 'Mozilla/5.0 (Linux; Android 8.0.0; Pixel 2 XL Build/OPD1.170816.004) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.72 Mobile Safari/537.36'

with open("chalicelib/zerns", "rb") as f:
    zerns = pickle.load(f)
       
with open("chalicelib/indexTags.json", "r") as f:
    indexTags = json.load(f)
    
    

### Download Retry Decorator ###


def downloadRetry(func, serverErrorCodes, authenticationErrorCodes):
    @wraps(func)
    def wrapper(*args, **kwargs):
        data = func(*args, **kwargs)
        argsString = [repr(a)  for a in args]                     
        kwargsString = [f"{k}={v!r}"  for k, v in kwargs.items()]  
        signature = ", ".join(argsString + kwargsString)
        
        retries = 0
        
        if data.status_code != 200:
            logging.info(f"{data.text} for {args[-1]} API call")
            logging.info(f'Signature: {signature}')
            logging.info(f'Error Type: {data.status_code}')
            logging.info(f'Response Data: {data.text}')
            
            
        ### Log Unanticipated Errors and Retry I guess? ### 
        
        while retries < 5 and data.status_code not in serverErrorCodes + authenticationErrorCodes + [200]:
            print(f"Unanticipated Error => {data.status_code}")
            logging.info(f"Unanticipated Error => {data.status_code}")
            #time.sleep(retries*10 + 10)
            data = func(*args, **kwargs)
            retries += 1
        
            
        ### Do something for Server Errors ###
        
        while retries < 5 and data.status_code in serverErrorCodes:
            logging.info(f'Retry: Attempt No.{retries+1} after sleeping for {retries*10 + 10} seconds')
            time.sleep(retries*10 + 10)
            data = func(*args, **kwargs)
            retries += 1
            
            
        ### Do something for Client Fuckery ###
        ### Like token refresh for example ###
            
        while retries < 5 and data.status_code in authenticationErrorCodes:
            vaxxer = args[0]
            #time.sleep(3)
            
            
            #if data.headers.get('content-type') == 'application/json':
            try:
                jsonResponse = data.json()
                logging.info(f"JSON Response - {jsonResponse} with Error Code - {data.status_code}")
                if jsonData.get("errorCode") not in ["APPOIN0045", "APPOIN0040"]:
                    logging.info(f'Retry: Attempt No.{retries+1}')
                    vaxxer.refreshToken()
                    
                else:
                    # CAPTCHA failed so generate another
                    newCaptchaText = vaxxer.refreshCaptcha()
                    args[2]["captcha"] = newCaptchaText
                    
            except:
                textResponse = data.text
                logging.info(f"Text Response - {textResponse} with Error Code - {data.status_code}")
                if textResponse == "Unauthenticated access!":
                    logging.info(f'Retry: Attempt No.{retries+1}')
                    vaxxer.refreshToken()
                    
                
            data = func(*args, **kwargs)
            retries += 1
            
            
        return data
    
        
    return wrapper

       
downloadRetryer = partial(downloadRetry, 
                          serverErrorCodes=[429, 408, 500, 502, 504], 
                          authenticationErrorCodes = [401, 400])

### CONSTANTS ###

BENEFICIARIESuRL = "https://cdn-api.co-vin.in/api/v2/appointment/beneficiaries"
OTPgENuRL = "https://cdn-api.co-vin.in/api/v2/auth/generateMobileOTP"
SECRET = "U2FsdGVkX18s/oUTUJOmDy27XnsU5MQK+iwUroz0Qt8GFhlG76l3NzNxxJxtm2BptyYFmTQCHA+x1KfO+8iwag=="
OTPvALIDATEuRL = "https://cdn-api.co-vin.in/api/v2/auth/validateMobileOtp"
GETsESSIONSuRL = "https://cdn-api.co-vin.in/api/v2/appointment/sessions/public/findByDistrict?district_id={DID}&date={DS}"
SCHEDULEuRL = "https://cdn-api.co-vin.in/api/v2/appointment/schedule"
CAPTCHAdECODEuRL = "http://localhost:8000" # Or your chalice deployed url. Not required anymore.

baseHeaders = {
    "user-agent": "Mozilla/5.0 (Linux; Android 8.0.0; Pixel 2 XL Build/OPD1.170816.004) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.72 Mobile Safari/537.36", 
    "content-type": "application/json", 
    "origin": "https://selfregistration.cowin.gov.in", 
    "sec-fetch-site": "cross-site", 
    "sec-fetch-mode": "cors", 
    "sec-fetch-dest": "empty", 
    "referer": "https://selfregistration.cowin.gov.in/"
}

minHeaders = {"user-agent" : baseHeaders["user-agent"]}
authHeaders = deepcopy(baseHeaders)


### Astra Janaka / Markata Malayudha Praptirastu! ###


@dataclass
class Vaxxer:        
    phoneNumber: int
    districtIds: tuple # Comma separated IDs of districts where you want to get vaccinated
    kvdbBucket: str # Sign up and create a bucket from here => https://kvdb.io/start
    delay: float = 2.8 # Increase / Decrease this based on how the rate limit evolves
    preferredVaccines: str = "COVISHIELD"
    ageLimit: int = 18
    doseNumber: int = 1
    scheduled: bool = False # Flag for stopping the script. Changes when all beneficiaries get appointments
    otp: str = 'NA'
    otpUsedForToken: str = 'NA'
    scheduledBeneficiaries: tuple = () # Beneficiaries with confirmed appointments in the current session
    otpGeneratedAt: int = 42
    otpCapturedAt: int = 84
    token: str = 'NA'
    tokenGeneratedAt: int = 126
    error: bool = False
    
        
        
    
    def __post_init__(self):
        logging.info("***** Session Init *****")
        print("***** Session Init *****")
        self.preferredVaccines = [x.strip() for x in self.preferredVaccines.split(",")]
        self.scheduledBeneficiaries = []
        self.otp = self.get(f"https://kvdb.io/{self.kvdbBucket}/{self.phoneNumber}", {}, "Last OTP [Post Init]").text.split(".")[0].split()[-1].strip()
        authHeaders["authorization"] = f'Bearer {self.token}'
        
        if f'{self.phoneNumber}.json' not in os.listdir():
            with open(f'{self.phoneNumber}.json', 'w') as f:
                json.dump(self.__dict__, f)
        else:
            with open(f'{self.phoneNumber}.json', 'r') as f:
                lastData = json.load(f)
                
            self.token = lastData["token"]
            self.otpGeneratedAt = lastData.get("otpGeneratedAt", 42)
            self.otpCapturedAt = lastData.get("otpCapturedAt", 52)
            self.tokenGeneratedAt = lastData.get("tokenGeneratedData", 62)
            self.scheduled = lastData.get("scheduled", False)
            self.otpServiceIsHavingIssuesProbably = 0
            self.scheduleAttempts = 1
            self.preferredVaccines = lastData.get("preferredVaccines")
            self.relevantSessions = lastData.get("relevantSessions")
            self.otpUsedForToken = lastData.get('otpUsedForToken')
                
                
                
    @downloadRetryer
    def post(self, url, dataDict, headers, purpose):
        if not self.error:
            if 'authorization' in headers:
                headers['authorization'] = f'Bearer {self.token}'
            logging.info(f"Begin => POST API call for {purpose}")
            resp = requests.post(url=url, json=dataDict, headers=headers)
            logging.info(f"End => POST API call for {purpose} {resp.status_code}")
            return resp
    

    @downloadRetryer
    def get(self, url, headers, purpose):
        if not self.error:
            if 'authorization' in headers:
                headers['authorization'] = f'Bearer {self.token}'
            logging.info(f"Begin => GET API call for {purpose}")
            resp = requests.get(url=url, headers=headers)
            logging.info(f"End => GET API call for {purpose} {resp.status_code}")
            return resp
                
            
        
    def loadUserData(self):
        with open(f'{self.phoneNumber}.json') as f:
            data = json.load(f)
        return data
    
    
    
    def modifyUserData(self, changes):
        with open(f'{self.phoneNumber}.json') as f:
            data = json.load(f)
            
        for key, value in changes.items():
            data[key] = value
        with open(f'{self.phoneNumber}.json', 'w') as f:
            json.dump(data, f)
        
        
        
    @limits(calls=3, period=300)   
    def generateOtp(self):
        otpGenResponse = self.post(OTPgENuRL, {"mobile" : self.phoneNumber, "secret" : SECRET}, minHeaders, "OTP Generation")
        self.otpGeneratedAt = nowStamp()[1]
        self.otpCaptured = False
        self.otpValidated = False
        self.txnId = otpGenResponse.json()['txnId']
        self.otpCaptureAttempts = 0
        self.modifyUserData({
            'otpGeneratedAt' : self.otpGeneratedAt, 
            'txnId' : otpGenResponse.json()['txnId'],
            'otpCaptured' : False,
            'otpCaptureAttempts' : 0
        })
            
        
    def getOtp(self):
        otpData = self.get(f"https://kvdb.io/{self.kvdbBucket}/{self.phoneNumber}", {}, "Get OTP").text
        otp = otpData.split(".")[0].split()[-1].strip()
        capturedAt = otpData.split("CoWIN")[-1].strip().replace("at", "").replace('"', '').strip()
        return {"otp" : otp, "capturedAt" : capturedAt}
    
    
    def postOtpCapture(self, otp1, otp2, n):
        self.otp = otp1
        self.otpServiceIsHavingIssuesProbably = 0
        self.lastOtp = otp2
        self.otpCaptured = True
        self.otpCapturedAt = nowStamp()[1]
        self.modifyUserData({
            'otpCapturedAt' : self.otpCapturedAt,
            'otp' : self.otp,
            'lastOtp' : self.lastOtp,
            'otpServiceIsHavingIssuesProbably' : self.otpServiceIsHavingIssuesProbably,
            'otpCaptured' : self.otpCaptured,
            'otpCaptureAttempts' : n
        })
        
        
    def validateOtp(self):
        otpHash = sha256(str(self.otp.strip()).encode("utf-8")).hexdigest()
        logging.info(f"OTP Validation API call")
        tokenData = self.post(OTPvALIDATEuRL, {"otp" : otpHash, "txnId": self.txnId}, baseHeaders, "Token Refresh")
        logging.info(str(tokenData.status_code), tokenData.json())
        token = tokenData.json()['token']
        tokenGeneratedAt = nowStamp()[1]
        self.token = token
        self.tokenGeneratedAt = tokenGeneratedAt
        self.otpUsedForToken = self.otp
        self.modifyUserData({'token' : token, "tokenGeneratedAt" : tokenGeneratedAt, "otpUsedForToken" : self.otp})
        authHeaders["authorization"] = f"Bearer {self.token}"
        
    
    def refreshToken(self):
        otpAtKvdbData = self.getOtp()
        otpAtKvdb = otpAtKvdbData["otp"]
        
        try:
            optAtKvdbCapturedAt = int(datetime.timestamp(datetime.strptime(otpAtKvdbData["capturedAt"], '%b %d, %Y %I:%M%p').astimezone()))
        except:
            optAtKvdbCapturedAt = 42
            
        # If the Token hasn't expired yet,
        # Get the fuck outta here.
        
        # It doesn't matter if we requested more OTPs
        # after this Token has been generated and 
        # maybe some of them were delivered to your device
        # and some weren't and out of those delivered
        # some were captured at KVDB and some weren't.
        # Because MULTIPLE VALID TOKENS CAN EXIST SIMULTANEOUSLY
        
    
        if nowStamp()[1] - self.tokenGeneratedAt < 890:
            return "Active Token Exists!"
        
        
        #else:
            
        # Lets look at different cases

        
        # Case 1
        # ======
        
        # OTP was captured < 3 minutes ago.
        # Token hasn't been generated yet against this
        # (We wouldn't be here otherwise)
        # We try to validate the OTP, generate a new Token 
        # and go about our business
        
        if nowStamp()[1] - optAtKvdbCapturedAt <= 160 and \
        self.otpUsedForToken != otpAtKvdb:
            self.postOtpCapture(otpAtKvdb, "NA", 0)
            self.validateOtp()
            

        
        
        # Case 2
        # ======
        # OTP was captured > 3 minutes ago
        # Doesn't matter whether a Token has been generated against this,
        # We request for a New OTP 
        # And go ahead with our business
            
            
        elif nowStamp()[1] - optAtKvdbCapturedAt > 160:
            self.generateOtp()
            while not self.otpCaptured and self.otpCaptureAttempts < 10:
                time.sleep(5)
                self.otpCaptureAttempts += 1
                latestOtpAtKvdb = self.getOtp()["otp"]
                logging.info(f"Old OTP => {otpAtKvdb}, Latest OTP => {latestOtpAtKvdb}")
                if otpAtKvdb != latestOtpAtKvdb:
                    self.postOtpCapture(latestOtpAtKvdb, otpAtKvdb, self.otpCaptureAttempts)
                    self.validateOtp()

        
        
        
        if self.otpCaptured == False:
            print("OTP Service is facing Issues OR Something is wrong with your OTP Automation")
            print("Check your OTP Automation Service logs")
            print("Check SMS logs on your phone")
            print("Try logging in from COWIN website and see if you are able to receive SMS")
            print("Check the logs in app.log file")
            logging.warning("OTP Service is facing Issues OR Something is wrong with your OTP Automation")
            self.scheduled = -1
            self.error = True
            
    
            
               
    def getBeneficiaries(self):
        beneficiariesData = self.get(BENEFICIARIESuRL, authHeaders, "Getting Beneficiaries")
        beneficiaries = beneficiariesData.json()["beneficiaries"]
        beneficiaryIds = [x['beneficiary_reference_id'] for x in beneficiaries if  x["vaccination_status"] == "Not Vaccinated" and not x.get("appointments")]
        todayString = "-".join(reversed(str(nowStamp()[0]).split()[0].split("-")))
        unscheduledBenificiaries = beneficiaryIds
        self.unscheduledBenificiaries = unscheduledBenificiaries
        
        if unscheduledBenificiaries == []:
            self.scheduled = True
        
        logging.info('Beneficiaries without Appointments: ' + ', '.join([' => '.join([x["name"], x["beneficiary_reference_id"]]) for x in beneficiaries \
              if x ["beneficiary_reference_id"] in unscheduledBenificiaries]))
    
    
    def getSessions(self):
        sessions = []
        todayString = "-".join(reversed(str(nowStamp()[0]).split()[0].split("-")))
        for districtId in self.districtIds:
            time.sleep(self.delay)
            logging.info(f"Sessions API call")
            sessionsUrl = GETsESSIONSuRL.replace("{DID}", districtId).replace("{DS}", todayString)
            districtSessionsResponse = self.get(sessionsUrl,authHeaders, "Get District Sessions")
            try:
                districtSessions = districtSessionsResponse.json()
                sessions.append(districtSessions["sessions"])
            except:
                print(districtSessionsResponse.text)

        sessions = [x for y in sessions for x in y]
        relevantSessions = [x for x in sessions if x['min_age_limit'] == self.ageLimit \
                            and x[f"available_capacity_dose{self.doseNumber}"] > 0 \
                            and x['fee_type'] != 'Free' \
                            and x['vaccine'] in self.preferredVaccines]
        
        if relevantSessions:
            logging.info(f"Available Sessions: {relevantSessions}")
            print("Sessions Available!")
        self.relevantSessions = relevantSessions
        self.modifyUserData({
            'relevantSessions' : self.relevantSessions
        })

        
    def refreshCaptcha(self):
        print("Refreshing CAPTCHA")
        logging.info("Refreshing CAPTCHA")
        logging.info(f"Scheduling API call at {nowStamp()[1]}")
        captchaUrl = "https://cdn-api.co-vin.in/api/v2/auth/getRecaptcha"
        capt = self.post(captchaUrl, {}, authHeaders, "Captcha Generation")
        svgtext = capt.json()['captcha']
        logging.info(f"Captcha SVG => {svgtext}")
        # Get the CAPTCHA decoding server running on port 8000 first
        captcha = requests.post(CAPTCHAdECODEuRL, 
                             json={"captcha" : svgtext}).text

        logging.info(f"Captcha Text => {captcha}")
        return captcha
        
    
    def bookAppointment(self):                    
        for session in self.relevantSessions:
            for slot in session["slots"]:
                for unscheduledBeni in self.unscheduledBenificiaries:
                    if unscheduledBeni not in self.scheduledBeneficiaries:
                        print(f"Scheduling API call at {nowStamp()[1]}")
                        logging.info(f"Scheduling API call at {nowStamp()[1]}")
                        captchaUrl = "https://cdn-api.co-vin.in/api/v2/auth/getRecaptcha"
                        capt = self.post(captchaUrl, {}, authHeaders, "Captcha Generation")
                        svgtext = capt.json()['captcha']
                        logging.info(f"Captcha SVG => {svgtext}")
                        # Get the CAPTCHA decoding server running on port 8000 first
                        # captchaDecoded = requests.post(CAPTCHAdECODEuRL, 
                        #                      json={"captcha" : svgtext}).text
                        
                        ### Decode CAPTCHA wiht a function
                        captchaDecoded = capchaxMacha(svgtext)
                        
                        logging.info(f"Captcha Text => {captchaDecoded}")

                        scheduleResponse = self.post(
                            SCHEDULEuRL, 
                            {
                                "dose": int(self.doseNumber),
                                "session_id": session["session_id"],
                                "slot": slot,
                                "beneficiaries": [unscheduledBeni],
                                "captcha" : captchaDecoded
                            },
                            authHeaders,
                            "Appointment Booking"
                        )
                        if scheduleResponse.status_code in [200, 201]:
                            print("*****************")
                            print(unscheduledBeni)
                            logging.info(f"APPOINTMENT CONFIRMED for {unscheduledBeni}")
                            print("*****************")
                            self.scheduledBeneficiaries += [unscheduledBeni]
                            
                            if set(self.scheduledBeneficiaries) == set(self.unscheduledBenificiaries):
                                self.scheduled = True
                                print("**** HURRAY! All done! ****")
                                logging.info("**** Session End ****")
                                
                        else:
                            print(scheduleResponse.status_code, scheduleResponse.text)
                                
                                
                                
    def run(self):
        try:
            while not self.scheduled:
                logging.info(f"Appointment Scheduling Attempt No.{self.scheduleAttempts}")
                print(f"Appointment Scheduling Attempt No.{self.scheduleAttempts} at {nowStamp()[1]}")
                self.getBeneficiaries()
                self.getSessions()
                self.bookAppointment()
                self.scheduleAttempts += 1
                
                
        # Happens when self.error == True which leads to None object
        # being returned as response to self.get and self.post methods
        # self.error becomes True when > 20 OTP capture attempts from kvdb.io 
        # don't return the new OTP
        
        # Possible Reasons
        
        # 1) OTP generation issues from server
        # 2) Issues with your OTP Automation tool - IFTTP / Shortcuts
        # 3) Unidentified bug in this code
        
        # Check your SMS logs, OTP Automation tool logs and logs for this session
        # Restart the script
        
        except AttributeError:
            print("**** Session End ****")
            logging.error("**** Session End ****")
            
            
            
        # Happens when more than 3 OTP generation requests are made within
        # a span of 5 minutes
        # 
            
            
        except RateLimitException:
            print("Pausing Code Execution for 5 minutes to prevent too many OTP requests")
            logging.info("Pausing Code Execution for 5 minutes to prevent too many OTP requests")
            time.sleep(300)
            self.run()


        
        
### CAPTCHA HELPERS  ###


def capchaxMacha(svgtext):
      #svgtext = svgData['captcha']
      randStr = ''.join(choice(string.ascii_uppercase + \
        string.digits) for _ in range(10))
      svg2png(bytestring=re.sub('(<path d=)(.*?)(fill=\"none\"/>)',
                                '',svgtext),  
              write_to=f"{randStr}.png")

      path = randStr + ".png"
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
