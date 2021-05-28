################# HOW TO RUN THIS ##################

#### 1) Create a bucket on KVDB => https://kvdb.io/start
#### 2) Configure IFTTT / Shortcuts on your phone.
####    Refer to this => https://github.com/bombardier-gif/covid-vaccine-booking#ifttt-steps-in-screenshots
####    Add the KVDB URL to IFFT / Shortcuts as elaborated in the link above
#### 3) Start chalice server for decoding CAPTCHA => https://github.com/janghaludu/cowin-captcha
####    Alternatively you can simply import functions from app.py in root folder and change a line here
#### 4) booker.log is where logs are stored. {phoneNumber}.json is where the state of booking is stored


# vaccer = Vaxxer(9999999999, ["581"], '4s7oQvpnybgERS9ftC3duv4', 3, "COVISHIELD, COVAXIN, SPUTNIK", 18, 1)
# vaccer.run()

# 9999999999 => phoneNumber
# ["581"] => Array of District codes
# 4s9oQjpnybpGS9ftZ3duv4 => Your KVDB bucket name
# 3 => Delay in seconds to avoid 429 errors
# "COVISHIELD, COVAXIN, SPUTNIK" => Preferred Vaccines
# 18 => 18 or 45 for age limit
# 1 => 1 or 2 for Dose Number

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

logging.basicConfig(filename='booker.log', filemode='w', 
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    level=logging.INFO)



def nowStamp(): return datetime.utcnow(), int((datetime.utcnow() - datetime(1970, 1, 1)).total_seconds())


### Download Retry Decorator ###


def downloadRetry(func, serverErrorCodes, authenticationErrorCodes):
    @wraps(func)
    def wrapper(*args, **kwargs):
        data = func(*args, **kwargs)
        argsString = [repr(a)  for a in args]                     
        kwargsString = [f"{k}={v!r}"  for k, v in kwargs.items()]  
        signature = ", ".join(argsString + kwargsString)
        
        retries = 0
        
        ### Do something for Server Errors ###
        
        while retries < 5 and data.status_code not in [200, 201] and data.status_code in serverErrorCodes:
            logging.info(f"{data.text} for {args[-1]} API call")
            logging.info(f'Signature: {signature}')
            logging.info(f'Error Type: {data.status_code}')
            logging.info(f'Retry: Attempt No.{retries+1} after sleeping for {retries*10 + 10} seconds')
            
            time.sleep(retries*10 + 10)
            data = func(*args, **kwargs)
            retries += 1
            
        ### Do something for Client Fuckery ###
        ### Like token refresh for example ###
            
        while retries < 5 and data.status_code in authenticationErrorCodes:
            logging.info(f"{data.text} for {args[-1]} API call")
            logging.info(f'Signature: {signature}')
            logging.info(f'Error Type: {data.status_code}')
            logging.info(f'Retry: Attempt No.{retries+1} after sleeping for {retries*10 + 10} seconds')
            vaxxer = args[0]
            if retries != 0:
                time.sleep(retries*10 + 10)
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
CAPTCHAdECODEuRL = "http://localhost:8000"

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
        self.lastOtp = self.get(f"https://kvdb.io/{self.kvdbBucket}/{self.phoneNumber}", {}, "Last OTP [Post Init]").text.split(".")[0].split()[-1].strip()
        authHeaders["authorization"] = f'Bearer {self.token}'
        if f'{self.phoneNumber}.json' not in os.listdir():
            with open(f'{self.phoneNumber}.json', 'w') as f:
                json.dump(self.__dict__, f)
        else:
            with open(f'{self.phoneNumber}.json', 'r') as f:
                lastData = json.load(f)
            self.token = lastData["token"]
            self.otpGeneratedAt = lastData.get("otpGeneratedAt", "NA")
            self.otpCapturedAt = lastData.get("otpCapturedAt", "NA")
            self.tokenGeneratedAt = lastData.get("tokenGeneratedData", "NA")
            self.scheduled = lastData.get("scheduled", False)
            self.otpServiceIsHavingIssuesProbably = 0
            self.scheduleAttempts = 1
            self.preferredVaccines = lastData.get("preferredVaccines")
            self.relevantSessions = lastData.get("relevantSessions")
                
                
                
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
        lastOtp = self.lastOtp
        otpGenResponse = self.post(OTPgENuRL, {"mobile" : self.phoneNumber, "secret" : SECRET}, minHeaders, "OTP Generation")
        self.otpGeneratedAt = nowStamp()[1]
        self.newOtpCaptured = False
        self.txnId = otpGenResponse.json()['txnId']
        self.newOtpCaptured = False
        self.otpCaptureAttempts = 0
        self.modifyUserData({
            'otpGeneratedAt' : self.otpGeneratedAt, 
            'lastOtp' : lastOtp, 
            'txnId' : otpGenResponse.json()['txnId'],
            'newOtpCaptured' : False,
            'otpCaptureAttempts' : 0
        })
            
        
            
                        

    def getOtp(self):
        otp = self.get(f"https://kvdb.io/{self.kvdbBucket}/{self.phoneNumber}", {}, "Get OTP").text.split(".")[0].split()[-1].strip()
        return otp
    
    
    def refreshToken(self):
        self.generateOtp()
        self.otpCaptureAttempts = 0
        while not self.newOtpCaptured and self.otpCaptureAttempts < 10:
            time.sleep(5)
            otp = self.getOtp()
            logging.info(f"OTP => {otp}, Last OTP => {self.lastOtp}")
            self.otpCaptureAttempts += 1
            
            if otp != self.lastOtp:
                self.otpServiceIsHavingIssuesProbably = 0
                self.otp = otp
                self.newOtpCaptured = True
                self.otpCapturedAt = nowStamp()[1]
                self.modifyUserData({
                    'otpCapturedAt' : self.otpCapturedAt,
                    'otp' : otp,
                    'otpServiceIsHavingIssuesProbably' : 0
                })
                
        self.modifyUserData({
            'newOtpCaptured' : self.newOtpCaptured,
            'otpCaptureAttempts' : self.otpCaptureAttempts,
        })
        
        if self.newOtpCaptured == False:
            self.otpServiceIsHavingIssuesProbably += 10
            self.otp = self.lastOtp
            
            if self.otpServiceIsHavingIssuesProbably >= 20:
                print("OTP Service is facing Issues OR Something is wrong with your OTP Automation")
                print("Check your OTP Automation Service logs")
                print("Check SMS logs on your phone")
                print("Try logging in from COWIN website and see if you are able to receive SMS")
                print("Check the logs in app.log file")
                logging.warning("OTP Service is facing Issues OR Something is wrong with your OTP Automation")
                self.scheduled = -1
                self.error = True
            
        otpHash = sha256(str(self.otp.strip()).encode("utf-8")).hexdigest()
        logging.info(f"OTP Validation API call")
        tokenData = self.post(OTPvALIDATEuRL, {"otp" : otpHash, "txnId": self.txnId}, baseHeaders, "Token Refresh")
        logging.info(str(tokenData.status_code), tokenData.json())
        token = tokenData.json()['token']
        tokenGeneratedAt = nowStamp()[1]
        self.token = token
        self.otp = otp
        self.tokenGeneratedAt = tokenGeneratedAt
        self.modifyUserData({'token' : token, "otp" : otp, "tokenGeneratedAt" : tokenGeneratedAt})
        authHeaders["authorization"] = f"Bearer {self.token}"
                
            
            
    def getBeneficiaries(self):
        beneficiariesData = self.get(BENEFICIARIESuRL, authHeaders, "Getting Beneficiaries")
        beneficiaries = beneficiariesData.json()["beneficiaries"]
        beneficiaryIds = [x['beneficiary_reference_id'] for x in beneficiaries if  x["vaccination_status"] == "Not Vaccinated"]
        todayString = "-".join(reversed(str(nowStamp()[0]).split()[0].split("-")))
        unscheduledBenificiaries = beneficiaryIds
        self.unscheduledBenificiaries = unscheduledBenificiaries
        
        logging.info('No Appointment Bunnies: ' + ', '.join([' => '.join([x["name"], x["beneficiary_reference_id"]]) for x in beneficiaries \
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

    
    def bookAppointment(self):                    
        for session in self.relevantSessions:
            for slot in session["slots"]:
                for unscheduledBeni in self.unscheduledBenificiaries:
                    if unscheduledBeni not in self.scheduledBeneficiaries:
                        print(f"Scheduling API call at {nowStamp()[1]}")
                        captchaUrl = "https://cdn-api.co-vin.in/api/v2/auth/getRecaptcha"
                        capt = self.post(captchaUrl, {}, authHeaders, "Captcha Generation")
                        svgtext = capt.json()['captcha']
                        print(svgtext)
                        # Get the CAPTCHA decoding server running on port 8000 first
                        captcha = requests.post(CAPTCHAdECODEuRL, 
                                             json={"captcha" : svgtext}).text
                        
                        print(captcha)

                        scheduleResponse = self.post(
                            SCHEDULEuRL, 
                            {
                                "dose": int(self.doseNumber),
                                "session_id": session["session_id"],
                                "slot": slot,
                                "beneficiaries": [unscheduledBeni],
                                "captcha" : captcha

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
            while not self.scheduled and self.otpServiceIsHavingIssuesProbably < 20:
                logging.info(f"Appointment Scheduling Attempt No.{self.scheduleAttempts}")
                print(f"Appointment Scheduling Attempt No.{self.scheduleAttempts} at {nowStamp()[1]}")
                self.getBeneficiaries()
                self.getSessions()
                self.bookAppointment()
                self.scheduleAttempts += 1
                
        # Happens when there is an issue with OTP delivery or your OTP Automation tool.
        # Stops Code Execution
        except AttributeError:
            print("**** Session End ****")
            logging.error("**** Session End ****")
            
        # For handling infinite requests to OTP generation API  
        except RateLimitException:
            print("Pausing Code Execution for 5 minutes to prevent too many OTP requests")
            logging.info("Pausing Code Execution for 5 minutes to prevent too many OTP requests")
            time.sleep(300)
            self.run()


        
        
