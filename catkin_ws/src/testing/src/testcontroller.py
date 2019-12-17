#!/usr/bin/env python

import matplotlib.pyplot as plot
import sys
import rospy

CONTROL = '!CTRL'


def access(access_point, path):
    attr = getattr(access_point, path[0])
    if len(path) > 1:
        return access(attr, path[1:])
    else:
        return attr


class MessageFinder:
    def __init__(self, topic, content, msg_type=None, target_msg_attr=None):        
        self.topic = topic
        self.Msg = None
        self.type = msg_type
        if type(content) is str:
            self.content = {content: CONTROL}
            self.target_msg_attr = content
        elif type(content) is dict:
            self.content = content
        else:
            raise ValueError(str(content) + ": not dict or string")
        
        if target_msg_attr is None:
            for attr, value in self.content.items():
                if value == CONTROL:
                    self.target_msg_attr = attr
                    break         

        if self.type is None:
            topics_list = rospy.get_published_topics()
            for name, typ in topics_list:
                if topic == name:
                    self.type = typ
        
        if self.type is None:
            raise ValueError('message not found: %s' % (topic,))
        else:
            type_path = self.type.split('/')
            type_path.insert(-1,'msg')

            msg_class_path = reduce(lambda x,y: x+'.'+y, type_path[:-1])

            exec("import " + msg_class_path)
            print("imported " + msg_class_path)

            msg_module = sys.modules[type_path[0]]
            self.Msg = access(msg_module, type_path[1:])
            
    
    def create_subscriber(self, callback_fnc, callback_fnc_args=None):
        return rospy.Subscriber(self.topic, self.Msg, callback=callback_fnc, callback_args=callback_fnc_args)

    def create_publisher(self):
        return rospy.Publisher(self.topic, self.Msg)

    def extract_data(self, msg):
        return getattr(msg, self.target_msg_attr)
    
    def create_controlmsg(self, data_value):
        msg = self.Msg()
        for attr, value in self.content.items():
            if value == CONTROL:     
                setattr(msg, attr, data_value)
            else:
                setattr(msg, attr, value)
        return msg


class DataSeries:
    def __init__(self, x_axis_name='x', y_axis_name='y'):
        self.x_values = []
        self.y_values = []
        self.xlabel = x_axis_name
        self.ylabel = y_axis_name
       
    def __iadd__(self, data):
        self.x_values.append(data[0])
        self.y_values.append(data[1])
        return self
    
    def __str__(self):
        text = ""
        for i in range(len(self.x_values)):
            text += "(%f,%f) " % (self.x_values[i], self.y_values[i])
        return text


class DataCollector:
    def __init__(self, commander, observers):
        self.timeout = commander.lin_ctrl.duration
        self.obsrs = observers# list of Observers
        self.cmdr =commander# single Commander

    def run_test(self, t0='on call'):
        print('\n=======TEST=======')
        if self.cmdr is None:
            print('[CONTROL] None')
        else:
            print('[CONTROL]' + self.cmdr.msgf.topic)
        for obs in self.obsrs:
            print('[OBSERVING]' + obs.msgf.topic)
        
        rate = rospy.Rate(2)
        
        if t0 == 'on call':
            t0 = rospy.rostime.get_time()
        
        print('---BEGINING TEST\n')
            
        for obs in self.obsrs:
            obs.t0 = t0
            obs.active = True

        self.cmdr.t0 = t0
        self.cmdr.pub_commands()
        
        for obs in self.obsrs:
             obs.active = False

        print('\n---ENDING TEST')
        
        print('plotting results...')
        self.plot()
        
        
    def plot(self):
        colours = ['g','r','c','m' ,'y']
        for obs in self.obsrs:
            series = obs.data_series
            if len(colours) == 0:
                colour = 'k'
            else:
                colour = colours.pop(0)
            plot.plot(series.x_values, series.y_values, '.-' + colour, label='OBS:'+series.ylabel)
        series = self.cmdr.data_series
        plot.step(series.x_values, series.y_values, 'o--b', where='post', label='CTRL:'+series.ylabel)
        plot.xlabel(series.xlabel)
        plot.legend(title="LEGEND", fontsize='x-small')
        plot.show()


#gets data from subscriber
class Observer:
    def __init__(self, topic, msg_attr, msg_type=None):
        self.data_series = DataSeries('time', topic + "." + msg_attr)
       
        self.msgf = MessageFinder(topic, {msg_attr:CONTROL}, msg_type)

        if self.msgf.Msg == None:
            print("cannot observe unpublished topic: '" + topic + "'")
            self.sub = None
        else:
            self.sub = self.msgf.create_subscriber(self.read)
            self.active = False

        
    def read(self, data):
        if not self.active:
            return
        if self.t0 is None:
            print("t0 is None")
            self.t0 = 0

        if self.data_series.xlabel == 'time':
            x = rospy.rostime.get_time() - self.t0
        else:
            x = rospy.rostime.get_time() - self.t0# because there is no other option for independent variables      
        
        self.data_series += [x, self.msgf.extract_data(data)]


class LinearControl:
    '''
    y2 |          *
       |
    y0 |*
     0 ---------------
       0
       |<-------->|
           duration
    '''
    def __init__(self, duration, y0, y1=None):
        self.y0 = y0
        if y1 is None:
            self.y1 = y0#automatically a constant value
        else:
            self.y1 = y1
        self.duration = duration
        #dt is delta time or duration

    def interplorate(self, x):
        return (self.y1 - self.y0) / self.duration * x + self.y0



#publishes control signals
class Commander:
    def __init__(self, topic, msg_content, msg_type, timeout, y0, y1=None, command_rate=0):
        self.msgf = MessageFinder(topic, msg_content, msg_type)
        self.pub = rospy.Publisher(topic, self.msgf.Msg, queue_size=10)
        self.data_series = DataSeries('time', topic + "." + self.msgf.target_msg_attr)
        self.lin_ctrl = LinearControl(timeout, y0, y1)
        self.rate = command_rate

    def pub_commands(self):
        if self.t0 is None:
            print("t0 is None")
            self.t0 = 0

        rospy_rate = rospy.Rate(10)
        if self.rate > 0:
            rospy_rate = rospy.Rate(self.rate)            

        t2 = self.lin_ctrl.duration + self.t0

        while not rospy.is_shutdown() and rospy.rostime.get_time() < t2:
            data = self.create_control_data()
            msg = self.msgf.create_controlmsg(data[1])
            self.pub.publish(msg)
            self.print_controlmsg_info(data)

            if self.rate > 0:
                rospy_rate.sleep()
            else:
                while not rospy.is_shutdown() and rospy.rostime.get_time() < t2:
                    rospy_rate.sleep()
        
        data = self.create_control_data()#adding a point at the end allows plot to interplorate across test range
        self.print_controlmsg_info(data)
        
    def create_control_data(self):
        data = [None,None]
        data[0] = rospy.rostime.get_time() - self.t0
        data[1] = self.lin_ctrl.interplorate(data[0])
        self.data_series += data
        return data
    
    def print_controlmsg_info(self, data):
        print('@%s:{:.2f} %s<<{:.2f}'.format(data[0], data[1]) % (self.data_series.xlabel, self.data_series.ylabel))


def observer_arg(arg):
    #format: "/aquadrone/fake/out.data"
    arg = arg.split('.')
    for a in arg:
        if len(a) == 0:
            return None
    return Observer(arg[0], arg[1])

def commander_arg(arg, timeout, command_rate):
    #format: "/aquadrone/fake/in std_msgs/Float32 {'data':'!CTRL'} [10,20]"
    try:
        arg = arg.split(" ")
        exec("arg[2] = " + arg[2])
        exec("arg[3] = " + arg[3])
        return Commander(topic=arg[0], msg_content=arg[2], msg_type=arg[1], timeout=float(timeout),
                        y0=float(arg[3][0]),y1=float(arg[3][1]), command_rate=float(command_rate))
    except Exception as ex:
        print("FAILED TO PARSE COMMANDER ARG: " + str(ex))
        return None




rospy.init_node('test_controller') #INITIALIZE

#MessageFinder test code
msg = MessageFinder('/rosout', '')
if not repr(msg.Msg) == "<class 'rosgraph_msgs.msg._Log.Log'>":
    print('FUNCTIONALITY TEST-ERROR:', msg.Msg, "!= <class 'rosgraph_msgs.msg._Log.Log'>")


#ARGUMENTS
ARGS = {}
for arg in sys.argv[1:]:
    sep = arg.partition(":=")
    ARGS[sep[0]] = sep[2]

print(ARGS)


print('\n')
#MAIN

observers = map(observer_arg, rospy.get_param("test_control/observers").split(" "))


cmdr = commander_arg(rospy.get_param("test_control/commander"), ARGS["timeout"], ARGS["command_rate"])
if cmdr is None:
    quit()

clt = DataCollector(cmdr, observers)

clt.run_test()

# MAIN
print('\n')



'''
obs = Observer('aquadrone/sensors/out/pressure', 'fluid_pressure')
com = Commander('')

collect = DataCollector()

collect.run_test()
'''

    
