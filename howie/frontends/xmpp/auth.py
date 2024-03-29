##   auth.py
##
##   Copyright (C) 2003-2004 Alexey "Snake" Nezhdanov
##
##   This program is free software; you can redistribute it and/or modify
##   it under the terms of the GNU General Public License as published by
##   the Free Software Foundation; either version 2, or (at your option)
##   any later version.
##
##   This program is distributed in the hope that it will be useful,
##   but WITHOUT ANY WARRANTY; without even the implied warranty of
##   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
##   GNU General Public License for more details.

# $Id: auth.py,v 1.3 2004/10/12 17:32:01 cort Exp $

from protocol import *
from client import PlugIn
import sha,base64,random,dispatcher

import md5
def HH(some): return md5.new(some).hexdigest()
def H(some): return md5.new(some).digest()
def C(some): return ':'.join(some)

class NonSASL(PlugIn):
    def __init__(self,user,password,resource):
        PlugIn.__init__(self)
        self.DBG_LINE='gen_auth'
        self.user=user
        self.password=password
        self.resource=resource

    def plugin(self,owner):
        if not self.resource: return self.authComponent(owner)
        self.DEBUG('Querying server about possible auth methods','start')
        resp=owner.Dispatcher.SendAndWaitForResponse(Iq('get',NS_AUTH,payload=[Node('username',payload=[self.user])]))
        if not isResultNode(resp):
            self.DEBUG('No result node arrived! Aborting...','error')
            return
        iq=Iq(typ='set',node=resp)
        query=iq.getTag('query')
        query.setTagData('username',self.user)
        query.setTagData('resource',self.resource)

        if query.getTag('token'):
            token=query.getTagData('token')
            seq=query.getTagData('sequence')
            self.DEBUG("Performing zero-k authentication",'ok')
            hash = sha.new(sha.new(self.password).hexdigest()+token).hexdigest()
            for foo in xrange(int(seq)): hash = sha.new(hash).hexdigest()
            query.setTagData('hash',hash)
        elif query.getTag('digest'):
            self.DEBUG("Performing digest authentication",'ok')
            query.setTagData('digest',sha.new(owner.Dispatcher.Stream._document_attrs['id']+self.password).hexdigest())
        else:
            self.DEBUG("Sequre methods unsupported, performing plain text authentication",'warn')
            query.setTagData('password',self.password)
        resp=owner.Dispatcher.SendAndWaitForResponse(iq)
        if isResultNode(resp):
            self.DEBUG('Sucessfully authenticated with remove host.','ok')
            owner.User=self.user
            owner.Resource=self.resource
            owner._registered_name=owner.User+'@'+owner.Server+'/'+owner.Resource
            return 'ok'
        self.DEBUG('Authentication failed!','error')

    def authComponent(self,owner):
        self.handshake=0
        owner.send(Protocol('handshake',payload=[sha.new(owner.Dispatcher.Stream._document_attrs['id']+self.password).hexdigest()]))
        owner.RegisterHandler('handshake',self.handshakeHandler)
        while not self.handshake:
            self.DEBUG("waiting on handshake",'notify')
            owner.Process(1)
        owner._registered_name=self.user
        if self.handshake+1: return 'ok'

    def handshakeHandler(self,disp,stanza):
        if stanza.getName()=='handshake': self.handshake=1
        else: self.handshake=-1

class SASL(PlugIn):
    def __init__(self,username,password):
        PlugIn.__init__(self)
        self.DBG_LINE='SASL_auth'
        self.username=username
        self.password=password
        self.startsasl=None

    def plugin(self,owner):
        if self._owner.Dispatcher.Stream.features: self.FeaturesHandler(self._owner.Dispatcher,self._owner.Dispatcher.Stream.features)
        else: self._owner.RegisterHandler('features',self.FeaturesHandler)

    def FeaturesHandler(self,conn,feats):
        if not feats.getTag('mechanisms',namespace=NS_SASL):
            self.startsasl='failure'
            self.DEBUG('SASL not supported by server','error')
            return
        mecs=[]
        for mec in feats.getTag('mechanisms',namespace=NS_SASL).getTags('mechanism'):
            mecs.append(mec.getData())
        self._owner.RegisterHandler('challenge',self.SASLHandler)
        self._owner.RegisterHandler('failure',self.SASLHandler)
        self._owner.RegisterHandler('success',self.SASLHandler)
        if "DIGEST-MD5" in mecs:
            node=Node('auth',attrs={'xmlns':NS_SASL,'mechanism':'DIGEST-MD5'})
        elif "PLAIN" in mecs:
            sasl_data='%s\x00%s\x00%s'%(self.username+'@'+self._owner.Server,self.username,self.password)
            node=Node('auth',attrs={'xmlns':NS_SASL,'mechanism':'PLAIN'},payload=[base64.encodestring(sasl_data)])
        else:
            self.startsasl='failure'
            self.DEBUG('I can only use DIGEST-MD5 and PLAIN mecanisms.','error')
            return
        self.startsasl='in-process'
        self._owner.send(node.__str__())

    def SASLHandler(self,conn,challenge):
        if challenge.getNamespace()<>NS_SASL: return
        if challenge.getName()=='failure':
            self.startsasl='failure'
            try: reason=challenge.getChildren()[0]
            except: reason=challenge
            self.DEBUG('Failed SASL authentification: %s'%reason,'error')
            return
        elif challenge.getName()=='success':
            self.startsasl='success'
            self.DEBUG('Successfully authenticated with remote server.','ok')
            self._owner.Dispatcher.PlugOut()
            dispatcher.Dispatcher().PlugIn(self._owner)
            self._owner.User=self.username
            return
########################################3333
        incoming_data=challenge.getData()
        chal={}
        data=base64.decodestring(incoming_data)
        self.DEBUG('Got challenge:'+data,'ok')
        for pair in data.split(','):
            key,value=pair.split('=')
            if value[:1]=='"' and value[-1:]=='"': value=value[1:-1]
            chal[key]=value
        if chal.has_key('qop') and chal['qop']=='auth':
            resp={}
            resp['username']=self.username
            resp['realm']=self._owner.Server
            resp['nonce']=chal['nonce']
            cnonce=''
            for i in range(7):
                cnonce+=hex(int(random.random()*65536*4096))[2:]
            resp['cnonce']=cnonce
            resp['nc']=('00000001')
            resp['qop']='auth'
            resp['digest-uri']='xmpp/'
            A1=C([H(C([resp['username'],resp['realm'],self.password])),resp['nonce'],resp['cnonce']])
            A2=C(['AUTHENTICATE',resp['digest-uri']])
            response= HH(C([HH(A1),resp['nonce'],resp['nc'],resp['cnonce'],resp['qop'],HH(A2)]))
            resp['response']=response
            resp['charset']='utf-8'
            sasl_data=''
            for key in ['charset','username','realm','nonce','nc','cnonce','digest-uri','response','qop']:
                if key in ['nc','qop','response','charset']: sasl_data+="%s=%s,"%(key,resp[key])
                else: sasl_data+='%s="%s",'%(key,resp[key])
########################################3333
            node=Node('response',attrs={'xmlns':NS_SASL},payload=[base64.encodestring(sasl_data[:-1]).replace('\n','')])
            self._owner.send(node.__str__())
        elif chal.has_key('rspauth'): self._owner.send(Node('response',attrs={'xmlns':NS_SASL}).__str__())
        else: 
            self.startsasl='failure'
            self.DEBUG('Failed SASL authentification: unknown challenge','error')
            return

class Bind(PlugIn):
    def __init__(self):
        PlugIn.__init__(self)
        self.DBG_LINE='bind'
        self.bound=None

    def plugin(self,owner):
        if self._owner.Dispatcher.Stream.features: self.FeaturesHandler(self._owner.Dispatcher,self._owner.Dispatcher.Stream.features)
        else: self._owner.RegisterHandler('features',self.FeaturesHandler)

    def FeaturesHandler(self,conn,feats):
        if not feats.getTag('bind',namespace=NS_BIND):
            self.bound='failure'
            self.DEBUG('Server does not requested binding.','error')
            return
        if feats.getTag('session',namespace=NS_SESSION): self.session=1
        else: self.session=-1
        self.bound=[]

    def Bind(self,resource=None):
        while self.bound is None and self._owner.Process(1): pass
        if resource: resource=[Node('resource',payload=[resource])]
        else: resource=[]
        resp=self._owner.SendAndWaitForResponse(Protocol('iq',typ='set',payload=[Node('bind',attrs={'xmlns':NS_BIND},payload=resource)]))
        if isResultNode(resp):
            self.bound.append(resp.getTag('bind').getTagData('jid'))
            self.DEBUG('Successfully bound %s.'%self.bound[-1],'ok')
            resp=self._owner.SendAndWaitForResponse(Protocol('iq',typ='set',payload=[Node('session',attrs={'xmlns':NS_SESSION})]))
            if isResultNode(resp):
                self.DEBUG('Successfully opened session.','ok')
                return 'ok'
            else:
                self.DEBUG('Session open failed.','error')
                self.session=0
        elif resp: self.DEBUG('Binding failed: %s.'%resp.getTag('error'),'error')
        else:
            self.DEBUG('Binding failed: timeout expired.','error')
            return ''
