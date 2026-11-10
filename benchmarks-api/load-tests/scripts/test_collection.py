"""Synthetic binary fixtures; no generated data under results/."""
import struct,unittest
from parse_gatling_log import Decoder

def i(n):return struct.pack('>i',n)
def s(value):
 b=value.encode('latin1');return i(len(b))+b+(b'\0' if b else b'')
def cached(n,value):return i(n)+s(value)
def log():
 return b'\0'+s('3.14.9')+s('Test')+struct.pack('>q',1000000)+s('')+i(1)+s('scenario')+i(0)+b'\2'+i(0)+b'\1'+i(100)+b'\1'+i(0)+cached(1,'GET /messages')+i(101)+i(151)+b'\1'+cached(2,'')+b'\1'+i(0)+i(-1)+i(152)+i(5152)+b'\0'+cached(3,'timeout')+b'\2'+i(0)+b'\0'+i(5153)
class Tests(unittest.TestCase):
 def test_binary_success_failure_and_cache(self):
  rows,meta=Decoder(log()).parse();self.assertEqual(len(rows),2);self.assertEqual(rows[0]['duration_ms'],50);self.assertEqual(rows[1]['error_class'],'timeout');self.assertEqual(meta['users_started'],meta['users_ended'])
 def test_truncated(self):
  with self.assertRaises(ValueError):Decoder(log()[:-1]).parse()
 def test_invalid_record(self):
  with self.assertRaises(ValueError):Decoder(log()+b'\xff').parse()
 def test_unsupported_version(self):
  with self.assertRaises(ValueError):Decoder(log().replace(b'3.14.9',b'3.15.0')).parse()
if __name__=='__main__':unittest.main()
