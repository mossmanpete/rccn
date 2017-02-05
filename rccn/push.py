#!/usr/bin/env python
"""Get info from Riak about an extension"""
############################################################################
#
# RCCN is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# RCCN is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero Public License for more details.
#
# You should have received a copy of the GNU Affero Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
############################################################################

from config import *
import obscvty
import smtplib
import random
from optparse import OptionParser

def advise(msg):
    from email.mime.text import MIMEText
    text = """
Favor de intervenir y arreglar esta situacion manualmente.
    """
    mail = MIMEText(text + msg )
    mail['Subject'] = msg
    mail['From'] = 'postmaster@rhizomatica.org'
    mail['To'] = 'postmaster@rhizomatica.org'
    s = smtplib.SMTP('mail')
    s.sendmail('postmaster@rhizomatica.org', advice_email, mail.as_string())
    s.quit()

def check(recent, hours=2):
    """ Get sub from the local PG db and see their status in riak """
    cur = db_conn.cursor()
    if recent == 0:
        cur.execute("SELECT msisdn,name from Subscribers where authorized=1")
    if recent == 1:
        sql = "SELECT msisdn,created FROM Subscribers WHERE created > NOW() -interval '%s hours'" % hours
        cur.execute(sql)
    if cur.rowcount > 0:
        print 'Subscriber Count: %s ' % (cur.rowcount)
        _subs=cur.fetchall()
        n=cur.rowcount
        for msisdn,name in _subs:
            print '----------------------------------------------------'
            print "%s: Checking %s %s" % (n, msisdn, name)
            imsi=osmo_ext2imsi(msisdn)
            if imsi:
                print "Local IMSI: \033[96m%s\033[0m" % (imsi)
                get(msisdn, imsi)
            else:
                msg="""
                Local Subscriber %s from PG Not Found on OSMO HLR!
                """ % msisdn
                advise(msg)
                print "\033[91;1mLocal Subscriber from PG Not Found on OSMO HLR!\033[0m"
            n=n-1
        print '----------------------------------------------------\n'
        

def imsi_clash(imsi, ext1, ext2):
    msg = """
    Un IMSI no deberia de estar registrado y autorizado al mismo tiempo
    en mas que una comunidad.

    !! IMSI Clash between %s and %s for %s !! """ % (ext1,ext2,imsi)
    advise(msg)
    print "\033[91;1m" + msg + "\033[0m" 

def get(msisdn, imsi):
    """Do the thing"""
    riak_client = riak.RiakClient(
    host=riak_ip_address,
    pb_port=8087,
    protocol='pbc')
    bucket = riak_client.bucket('hlr')
    sub = Subscriber()
    num = Numbering()
    try:
        # We can end up with indexes that point to non existent keys.
        # so this might fail, even though later get_index() will return an IMSI key.
        riak_obj = bucket.get(imsi, timeout=RIAK_TIMEOUT)
        if riak_obj.exists:
            riak_ext=riak_obj.data['msisdn']
        else:
            print "\033[91;1m!! Didn't get hlr key for imsi %s\033[0m" % imsi
            riak_ext = False
        if riak_ext and (riak_ext != msisdn):
            imsi_clash(imsi, msisdn, riak_ext)
            return
        
        riak_imsi = bucket.get_index('msisdn_bin', msisdn, timeout=RIAK_TIMEOUT).results

        if len(riak_imsi) > 1:
            print "\033[91;1m More than ONE entry in this index! \033[0m"

        if not len(riak_imsi):
            print '\033[93mExtension %s not found\033[0m, adding to D_HLR' % (msisdn)
            sub._provision_in_distributed_hlr(imsi, msisdn)
        else:
            # Already checked if the ext in the imsi key matches osmo extension.
            # Now check if the key pointed to by the extension index matches the osmo imsi
            if imsi != riak_imsi[0]:
                print "\033[91;1mIMSIs do not Match!\033[0m (%s)" % riak_imsi[0]  
                print "Riak's %s points to %s" % (riak_imsi[0], num.get_msisdn_from_imsi(riak_imsi[0]))
                return False
            print 'Extension: \033[95m%s\033[0m-%s-\033[92m%s\033[0m ' \
                  'has IMSI \033[96m%s\033[0m' % (msisdn[:5], msisdn[5:6], msisdn[6:], riak_imsi[0])
            data = bucket.get(riak_imsi[0]).data
            if data['authorized']:
                print "Extension: Authorised"
            else:
                print "Extension: \033[91mNOT\033[0m Authorised, Fixing"
                data['authorized']=1
                fix = bucket.new(imsi, data={"msisdn": msisdn, "home_bts": config['local_ip'], "current_bts": data['current_bts'], "authorized": data['authorized'], "updated": int(time.time()) })
                fix.add_index('msisdn_bin', msisdn)
                fix.add_index('modified_int', int(time.time()))
                fix.store()
            if msisdn[:6] == config['internal_prefix'] and data['home_bts'] != config['local_ip']:
                print "\033[91;1mHome BTS does not match my local IP! Fixing..\033[0m"
                fix = bucket.new(imsi, data={"msisdn": msisdn, "home_bts": config['local_ip'], "current_bts": data['current_bts'], "authorized": data['authorized'], "updated": int(time.time()) })
                fix.add_index('msisdn_bin', msisdn)
                fix.add_index('modified_int', int(time.time()))
                fix.store()
            try:
                host = socket.gethostbyaddr(data['home_bts'])
                home = host[0]
                host = socket.gethostbyaddr(data['current_bts'])
                current = host[0]
            except Exception as ex:
                home = data['home_bts']
                current = data['current_bts']
            print " Home BTS: %s" % (home)
            print "Last Seen: %s, %s" % ( 
                  current, 
                  datetime.datetime.fromtimestamp(data['updated']).ctime() )
    except Exception as ex:
        print ex


def osmo_ext2imsi(ext):
    try:
        vty = obscvty.VTYInteract('OpenBSC', '127.0.0.1', 4242)
        cmd = 'show subscriber extension %s' % (ext)
        t = vty.command(cmd)        
        m=re.compile('IMSI: ([0-9]*)').search(t)
        if m:
            return m.group(1)
        else: 
            return False
    except:
        print sys.exc_info()[1]
        return False

if __name__ == '__main__':
    parser = OptionParser()
    parser.add_option("-c", "--cron", dest="cron", action="store_true",
        help="Running from cron, add a delay to not all hit riak at same time")
    parser.add_option("-r", "--recent", dest="recent",
        help="How many hours back to check for created Subscribers")

    (options, args) = parser.parse_args()
    
    if options.recent:
        if options.cron:
            wait=random.randint(0,15)
            print "Waiting %s seconds..." % wait
            time.sleep(wait)
        check(1,options.recent)
    else:
        if options.cron:
            wait=random.randint(0,60)
            print "Waiting %s seconds..." % wait
            time.sleep(wait)
        check(0)    
