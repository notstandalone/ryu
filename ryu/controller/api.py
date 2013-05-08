import eventlet
import sys
import inspect
from ryu.base import app_manager
from ryu.controller import handler
from ryu.controller import dpset
from ryu.ofproto.ofproto_parser import MsgBase
from ryu.lib.rpc import RpcSession, RpcMessage
from ryu.ofproto import ofproto_v1_0
from ryu.ofproto import ofproto_v1_0_parser
from ryu.ofproto import ofproto_v1_2
from ryu.ofproto import ofproto_v1_2_parser
from ryu.ofproto import ofproto_v1_3
from ryu.ofproto import ofproto_v1_3_parser


class OFWireRpcSession(object):
    def __init__(self, socket, dpset):
        self.socket = socket
        self.dpset = dpset
        self.session = RpcSession()

    def _find_ofp_cls(self, ofp_version, name):
        parser_name = 'ryu.ofproto.ofproto_v1_' + str(ofp_version - 1) + \
            '_parser'
        mod = sys.modules[parser_name]
        for i in inspect.getmembers(mod, lambda cls: (inspect.isclass(cls))):
            if i[0] == name:
                return i[1]
        return None

    def _ofp_handle_match(self, clses, params):
        match = clses()
        for k, v in params.items():
            if hasattr(match, 'set_' + k):
                getattr(match, 'set_' + k)(v)
        return match

    def _ofp_handle_params(self, dp, params):
        for k, v in params.items():
            if type(v) == dict:
                self._ofp_handle_params(dp, v)
                clses = self._find_ofp_cls(dp.ofproto.OFP_VERSION, k)
                if clses not None:
                    if issubclass(clses, MsgBase):
                        ins = clses(dp, **v)
                    else:
                        if k == 'OFPMatch' and dp.ofproto.OFP_VERSION > 2:
                            ins = self._ofp_handle_match(clses, v)
                        else:
                            ins = clses(**v)
                    params[k] = ins
                    return ins
                else:
                    for key, value in v.items():
                        params[k] = value
                        break
            elif type(v) == list:
                ins = []
                for i in v:
                    ins.append(self._ofp_handle_params(dp, i))
                params[k] = ins
            else:
                pass

    def ofp_handle_request(self, msg):
        dpid = msg[3][0]
        params = msg[3][1]
        dp = self.dpset.get(int(dpid))
        if dp is None:
            print self.dpset.get_all()
            r = self.session.create_response(msg[1], 0, 0)
            self.socket.sendall(r)

        for i in params:
            self._ofp_handle_params(dp, i)
            for k, v in i.items():
                dp.send_msg(v)

        r = self.session.create_response(msg[1], 0, 0)
        self.socket.sendall(r)

    def serve(self):
        while True:
            ret = self.socket.recv(4096)
            if len(ret) == 0:
                print "client disconnected"
                break
            messages = self.session.get_messages(ret)
            for m in messages:
                if m[0] == RpcMessage.REQUEST:
                    if m[2] == 0:
                        self.ofp_handle_request(m)
                    r = self.session.create_response(m[1], 0, 0)
                    self.socket.sendall(r)
                elif m[0] == RpcMessage.RESPONSE:
                    pass
                elif m[0] == RpcMessage.NOTIFY:
                    pass
                else:
                    print "invalid type", m


class RpcApi(app_manager.RyuApp):
    _CONTEXTS = {
        'dpset': dpset.DPSet,
    }

    def __init__(self, *args, **kwargs):
        self.dpset = kwargs['dpset']
        super(RpcApi, self).__init__(*args, **kwargs)
        self.server = eventlet.listen(('127.0.0.1', 6000))
        self.pool = eventlet.GreenPool()
        self.pool.spawn_n(self.serve)

    def serve(self):
        while True:
            sock, address = self.server.accept()
            print "accepted", address
            session = OFWireRpcSession(sock, self.dpset)
            self.pool.spawn_n(session.serve)

    @handler.set_ev_cls(dpset.EventDP, dpset.DPSET_EV_DISPATCHER)
    def handler_datapath(self, ev):
        if ev.enter:
            print "join", ev.dp.id
