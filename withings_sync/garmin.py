import cloudscraper
import re
import json
import logging

log = logging.getLogger('garmin')

URL_SSO_SIGNIN = 'https://sso.garmin.com/sso/signin'
URL_DASH = 'https://connect.garmin.com/modern/'

class AuthException(Exception):
    pass

class GarminConnect(object):

    def _get_session(self, record=None, username=None, password=None):
        session = cloudscraper.create_scraper()

        params = [
            ('service', 'https://connect.garmin.com/modern/'),
            ('gauthHost', 'https://sso.garmin.com/sso'),
            ('clientId', 'GarminConnect'),
            ('consumeServiceTicket', 'false'),
        ]

        res = session.get(URL_SSO_SIGNIN, params=params)
        if res.status_code != 200:
            raise Exception('No login form')

        # Lookup for CSRF token
        csrf = re.search(r'<input type="hidden" name="_csrf" value="(\w+)" />', res.content.decode('utf-8'))  # noqa
        if csrf is None:
            raise Exception('No CSRF token')
        csrf_token = csrf.group(1)
        log.debug('Found CSRF token {}'.format(csrf_token))

        headers = {
            'Origin': 'https://sso.garmin.com',
            'Referer': res.url,
        }
        session.headers.update(headers)

        data = {
          'username': username,
          'password': password,
          'embed': 'false',
          '_csrf': csrf_token,
        }

        res = session.post(URL_SSO_SIGNIN, params=params, data=data, headers=headers)

        if not res.ok:
            if res.status_code == 429:
                raise Exception('Authentication failed due to too many requests (429). Retry later...')  # noqa
            raise AuthException('Authentication failed.')

        # Check for cookie
        if 'GARMIN-SSO-GUID' not in session.cookies:
            raise Exception('Missing Garmin auth cookie')

        # Second auth step
        # Needs a service ticket from previous response
        headers = {
            'Host': 'connect.garmin.com',
        }
        res = session.get(URL_DASH, params=params, headers=headers)
        if res.status_code != 200 and not res.history:
            raise Exception('Second auth step failed.')

        # Check login
        res = session.get('https://connect.garmin.com/modern/currentuser-service/user/info')
        if not res.ok:
            raise Exception("Login check failed.")
        garmin_user = res.json()
        log.info('Logged in as {}'.format(garmin_user['username']))

        return session


    @staticmethod
    def get_json(page_html, key):
        """Return json from text."""
        found = re.search(key + r" = (\{.*\});", page_html, re.M)
        if found:
            json_text = found.group(1).replace('\\"', '"')
            return json.loads(json_text)
        return None


    def print_cookies(self, cookies):
        log.debug('Cookies: ')
        for key, value in list(cookies.items()):
            log.debug(' %s = %s', key, value)


    def login(self, username, password):
        num_retries = 5
        for x in range(0, num_retries):
            try:
                return self._get_session(username=username, password=password)
            except AuthException:
                log.debug("Login auth failed attempting retry {}".format(x))
        raise AuthException("Unable to authenticate login after {} retries".format(num_retries))



    def upload_file(self, f, session):
        files = {
            'data': (
                'withings.fit', f
            )
        }

        res = session.post('https://connect.garmin.com/modern/proxy/upload-service/upload/.fit',
                           files=files,
                           headers={'nk': 'NT'})

        try:
            resp = res.json()

            if 'detailedImportResult' not in resp:
                raise KeyError
        except (ValueError, KeyError):
            if res.status_code == 204:   # HTTP result 204 - 'no content'
                log.error('No data to upload, try to use --fromdate and --todate')
            else:
                log.error('Bad response during GC upload: %s', res.status_code)

        return res.status_code in [200, 201, 204]
