import unittest
from unittest.mock import patch
import uuid
import native_credentials as credentials


class MemoryVault:
    def __init__(self): self.values={}
    def read(self,service,account): return self.values.get((service,account))
    def write(self,service,account,token): self.values[service,account]=token
    def delete(self,service,account): self.values.pop((service,account),None)


class AccessScopeTests(unittest.TestCase):
    def test_update_and_access_namespaces_never_overlap(self):
        device=str(uuid.uuid4());vault=MemoryVault()
        with patch.object(credentials,'_backend',return_value=vault):
            for scope in credentials.SCOPES:
                credentials.write('mentat',device,'fixture-'+scope,scope)
            for scope in credentials.SCOPES:
                self.assertEqual(credentials.read('mentat',device,scope),'fixture-'+scope)
            self.assertEqual(credentials.read('mentat',device),'fixture-update')
            credentials.delete('mentat',device,'access-password')
            self.assertIsNone(credentials.read('mentat',device,'access-password'))
            self.assertEqual(credentials.read('mentat',device),'fixture-update')
            self.assertIsNone(credentials.read('melange',device,'access-token'))
            self.assertIsNone(credentials.read('mentat',str(uuid.uuid4()),'access-token'))

    def test_unknown_scope_or_oversize_secret_refused(self):
        device=str(uuid.uuid4())
        with self.assertRaises(credentials.CredentialError):credentials._identity('mentat',device,'arbitrary')
        with self.assertRaises(credentials.CredentialError):credentials._token_bytes('x'*2049)


if __name__=='__main__':unittest.main()
