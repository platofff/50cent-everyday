import json
import logging
import os
import random
import re
from io import BytesIO
from time import sleep
from typing import List, Dict, Union
from urllib import request, parse as urllib_parse
from urllib.error import HTTPError

import face_recognition
from PIL import Image, ImageFont, ImageDraw
from vk_api import vk_api, VkUpload

from audio import audios

logger = logging.getLogger(__name__)

NUM_JITTERS = 2
LAST_URLS_NUMBER = 10
VK_GROUP_ID = int(os.environ['VK_GROUP_ID'])

vk_session = vk_api.VkApi(token=os.environ["VK_TOKEN"])
vk = vk_session.get_api()


def download_image(url: str) -> BytesIO:
    with request.urlopen(request.Request(url, headers=ImgSearch.headers)) as response:
        image_data = response.read()
        return BytesIO(image_data)


class ImgSearch:
    _url = 'https://duckduckgo.com/'
    _requestUrl = 'https://duckduckgo.com/i.js'
    headers = {
        'authority': 'duckduckgo.com',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'sec-fetch-dest': 'empty',
        'x-requested-with': 'XMLHttpRequest',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/80.0.3987.163 Safari/537.36',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'cors',
        'referer': 'https://duckduckgo.com/',
        'accept-language': 'en-US,en;q=0.9',
    }

    @staticmethod
    def _get_images(objs: List[Dict[str, Union[str, int]]]) -> List[str]:
        return [obj['image'] for obj in objs if obj['image'].endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))]

    @staticmethod
    def search(keywords: str, s: int = 0) -> List[str]:
        params = {
            'q': keywords,
            't': 'ht',
            'iax': 'common',
            'ia': 'common'
        }
        logger.debug("Hitting DuckDuckGo for Token")

        #   First make a request to above URL, and parse out the 'vqd'
        #   This is a special token, which should be used in the subsequent request
        res = request.urlopen(
            request.Request(
                ImgSearch._url, data=urllib_parse.urlencode(params).encode()
            )
        ).read().decode('utf-8')

        search_obj = re.search(r'vqd=([\d-]+)&', res, re.M | re.I)

        if not search_obj:
            logger.debug('Token Parsing Failed !')
            return []

        logger.debug('Obtained Token')

        params = {
            'l': 'us-en',
            'o': 'json',
            'q': keywords,
            'vqd': search_obj.group(1),
            'f': ',,,',
            'p': '1',
            'v7exp': 'a',
            's': str(s),
            'u': 'bing'
        }

        logger.debug('Hitting Url : %s', ImgSearch._requestUrl)

        data = None
        while data is None:
            try:
                data = json.loads(
                    request.urlopen(
                        request.Request(
                            f'{ImgSearch._requestUrl}?{urllib_parse.urlencode(params).encode()}',
                            headers=ImgSearch.headers)
                    ).read().decode('utf-8')
                )
            except HTTPError as e:
                logger.debug(f'Got {e}, waiting 3 seconds')
                sleep(3)

        logger.debug('Hitting Url Success : %s', ImgSearch._requestUrl)
        return ImgSearch._get_images(data['results'])


def is_image_50cent(image_data: BytesIO) -> bool:
    # Load all reference images from 'ref' directory
    reference_dir = 'ref'
    reference_encodings = []

    for filename in os.listdir(reference_dir):
        if filename.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            filepath = os.path.join(reference_dir, filename)
            reference_image = face_recognition.load_image_file(filepath)
            encodings = face_recognition.face_encodings(reference_image, num_jitters=NUM_JITTERS)
            if encodings:
                reference_encodings.append(encodings[0])
            if not encodings:
                logger.info(f'Failed to detect face on {filename}')

    # Load the image
    unknown_image = face_recognition.load_image_file(image_data)

    # Check if the image has at least one face
    face_locations = face_recognition.face_locations(unknown_image)
    if not face_locations:
        return False

    # Get the face encoding for the unknown image
    unknown_encoding = face_recognition.face_encodings(unknown_image, face_locations, num_jitters=NUM_JITTERS)[0]

    # Compare the unknown face to the known faces of "50 Cent"
    results = face_recognition.compare_faces(reference_encodings, unknown_encoding, tolerance=0.9)

    # If the unknown face matches with any of the reference images, return True
    return any(results)


def last_urls() -> list[str]:
    try:
        with open('state/last_urls.txt', 'r') as f:
            return f.read().split('\n')
    except FileNotFoundError:
        return []


def write_urls(urls: list[str], new_url: str):
    n_urls = (urls + [new_url])[-LAST_URLS_NUMBER:]
    with open('state/last_urls.txt', 'w') as f:
        f.write('\n'.join(n_urls))


def find_50cent() -> BytesIO:
    urls = []
    lu = last_urls()
    while True:
        if not urls:
            urls = ImgSearch.search('50 cent rapper', random.randint(0, 10) * 100)
            urls = list(filter(lambda x: x not in lu, urls))
            continue

        index = random.randrange(len(urls))
        url = urls.pop(index)
        try:
            img = download_image(url)
        except:
            continue
        logger.info(f'Trying {url}')
        if is_image_50cent(img):
            write_urls(lu, url)
            return img


def get_usd_rub() -> float:
    with request.urlopen(
            'https://iss.moex.com/iss/statistics/engines/currency/markets/selt/rates.json?iss.meta=off') as response:
        data = json.load(response)
    return data['cbrf']['data'][0][0]


def price_to_words(price: float) -> str:
    kop = round(price * 100)
    roubles = kop // 100
    kop %= 100

    def rouble_declension(n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return 'рубль'
        elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 > 20):
            return 'рубля'
        else:
            return 'рублей'

    def kopeck_declension(n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return 'копейка'
        elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 > 20):
            return 'копейки'
        else:
            return 'копеек'

    return f'{roubles} {rouble_declension(roubles)} {kop} {kopeck_declension(kop)}'


def add_text(img, text):
    font_path = "impact.ttf"
    font_size = int(img.width * 0.05)
    font = ImageFont.truetype(font_path, font_size)

    draw = ImageDraw.Draw(img)

    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (img.width - text_width) / 2
    y = img.height - text_height * 2

    draw.text((x, y), text, font=font, fill="white", stroke_width=2, stroke_fill="black")

    return img


def get_next_audio() -> str:
    try:
        with open('state/audio.txt', 'r') as f:
            audio_id = int(f.read())
    except FileNotFoundError:
        audio_id = -1

    audio_id += 1
    if audio_id == len(audios):
        audio_id = 0

    with open('state/audio.txt', 'w') as f:
        f.write(str(audio_id))

    return audios[audio_id]


def make_post(image: BytesIO, audio: str):
    upload = VkUpload(vk)

    image_upload = upload.photo(photos=image, album_id=os.environ['VK_ALBUM_ID'], group_id=VK_GROUP_ID)[0]
    photo = f"photo{image_upload['owner_id']}_{image_upload['id']}"

    vk.wall.post(owner_id=-VK_GROUP_ID, from_group=1, attachments=','.join((photo, audio)))


def update_title(price: str):
    title = f'50 Cent ({price}) на каждый день'
    vk.groups.edit(group_id=VK_GROUP_ID, title=title)


def main():
    img_data = find_50cent()
    usd_rub = get_usd_rub()
    price_50cent = price_to_words(usd_rub / 2)
    logger.info(price_50cent)

    img = Image.open(img_data)
    img_with_text = add_text(img, price_50cent)
    img_data = BytesIO()
    img_with_text.save(img_data, format='jpeg')
    img_data.seek(0)
    make_post(img_data, get_next_audio())
    update_title(price_50cent)


if __name__ == '__main__':
    main()
