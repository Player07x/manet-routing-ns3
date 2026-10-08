#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/wifi-module.h"
#include "ns3/mobility-module.h"
#include "ns3/aodv-module.h"
#include "ns3/olsr-module.h"
#include "ns3/dsdv-module.h"
#include "ns3/flow-monitor-module.h"
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <set>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE ("ManetRoutingCompare");

struct resultados {
    uint32_t tx;
    uint32_t rx;
    uint64_t rxBytes;
    double pdr;
    double atrasoMs;
    double throughputMbps;
    double jitterMs;
};

resultados ExecutaSimulacao (const std::string &protocolo, uint32_t nNodes, double simTime, double velocidade, double area, uint32_t run){
    RngSeedManager::SetSeed (1);
    RngSeedManager::SetRun (run);

    //1.Nos
    NodeContainer nodes;
    nodes.Create (nNodes);

    //2. wifi ad hoc
    YansWifiChannelHelper channel = YansWifiChannelHelper::Default ();
    YansWifiPhyHelper phy;
    phy.SetChannel (channel.Create ());
    WifiMacHelper mac;
    mac.SetType ("ns3::AdhocWifiMac");
    WifiHelper wifi;
    NetDeviceContainer devices = wifi.Install (phy, mac, nodes);

    //3. Mobilidade
    std::ostringstream limite, vel;
    limite << "ns3::UniformRandomVariable[Min=0.0|Max=" << area << "]";
    vel << "ns3::ConstantRandomVariable[Constant=" << velocidade << "]";

    ObjectFactory pos;
    pos.SetTypeId ("ns3::RandomRectanglePositionAllocator");
    pos.Set ("X", StringValue (limite.str ()));
    pos.Set ("Y", StringValue (limite.str ()));
    Ptr<PositionAllocator> posAlloc = pos.Create ()->GetObject<PositionAllocator> ();

    MobilityHelper mobilidade;
    mobilidade.SetPositionAllocator (posAlloc);
    mobilidade.SetMobilityModel (
        "ns3::RandomWaypointMobilityModel",
        "Speed", StringValue (vel.str ()),
        "Pause", StringValue ("ns3::ConstantRandomVariable[Constant=1.0]"),
        "PositionAllocator", PointerValue (posAlloc)
    );
    mobilidade.Install (nodes);

    //4. AODV, OLSR ou DSDV
    InternetStackHelper stack;
    if (protocolo == "AODV"){
        AodvHelper aodv;
        stack.SetRoutingHelper (aodv);
        stack.Install (nodes);
    } else if (protocolo == "OLSR"){
        OlsrHelper olsr;
        stack.SetRoutingHelper (olsr);
        stack.Install (nodes);
    } else if (protocolo == "DSDV"){
        DsdvHelper dsdv;
        stack.SetRoutingHelper (dsdv);
        stack.Install (nodes);
    } else {
        NS_FATAL_ERROR ("Protocolo desconhecido: " << protocolo);
    }

    //5.IP
    Ipv4AddressHelper address;
    address.SetBase ("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer interfaces = address.Assign (devices);

    //6. Aplicativo UDP - 4 fluxos simultaneos (exigido pelo cenario-base)
    const uint32_t nFluxos = 4;
    uint16_t portas[nFluxos] = {9, 10, 11, 12};
    ApplicationContainer serverApps, clientApps;
    for (uint32_t f = 0; f < nFluxos; ++f){
        uint32_t origem = f;
        uint32_t destino = nNodes - 1 - f;

        UdpEchoServerHelper echoServer (portas[f]);
        ApplicationContainer serverApp = echoServer.Install (nodes.Get (destino));
        serverApp.Start (Seconds (1.0));
        serverApp.Stop (Seconds (simTime));
        serverApps.Add (serverApp);

        UdpEchoClientHelper echoClient (interfaces.GetAddress (destino), portas[f]);
        echoClient.SetAttribute ("MaxPackets", UintegerValue (100));
        echoClient.SetAttribute ("Interval", TimeValue (Seconds (1.0)));
        echoClient.SetAttribute ("PacketSize", UintegerValue (512));
        ApplicationContainer clientApp = echoClient.Install (nodes.Get (origem));
        clientApp.Start (Seconds (10.0));
        clientApp.Stop (Seconds (simTime));
        clientApps.Add (clientApp);
    }

    //7. Coleta de dados
    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll ();

    Simulator::Stop (Seconds (simTime));
    Simulator::Run ();

    monitor->CheckForLostPackets ();
    Ptr<Ipv4FlowClassifier> classifier = DynamicCast<Ipv4FlowClassifier> (flowmon.GetClassifier ());

    resultados r = {0, 0, 0, 0.0, 0.0, 0.0, 0.0};
    double somaAtraso = 0.0;
    double somaJitter = 0.0;
    std::set<uint16_t> portasFluxos = {9, 10, 11, 12};
    for (auto &par : monitor->GetFlowStats ()){
        Ipv4FlowClassifier::FiveTuple t = classifier->FindFlow (par.first);
        if (portasFluxos.count (t.destinationPort) > 0){
            r.tx += par.second.txPackets;
            r.rx += par.second.rxPackets;
            r.rxBytes += par.second.rxBytes;
            somaAtraso += par.second.delaySum.GetSeconds ();
            somaJitter += par.second.jitterSum.GetSeconds ();
        }
    }
    double duracaoUtil = simTime - 10.0; // janela entre o inicio dos clientes e o fim da simulacao
    r.pdr = (r.tx > 0) ? 100.0 * r.rx / r.tx : 0.0;
    r.atrasoMs = (r.rx > 0) ? 1000.0 * somaAtraso / r.rx : 0.0;
    r.throughputMbps = (duracaoUtil > 0) ? (r.rxBytes * 8.0) / (duracaoUtil * 1e6) : 0.0;
    r.jitterMs = (r.rx > 1) ? 1000.0 * somaJitter / (r.rx - 1) : 0.0;

    Simulator::Destroy ();
    return r;
};

    int main (int argc, char *argv[]){
        uint32_t nNodes = 20;
        double simTime = 120.0;
        uint32_t nRuns = 5;

        CommandLine cmd (__FILE__);
        cmd.AddValue ("nNodes", "Numero de nos", nNodes);
        cmd.AddValue ("simTime", "Tempo de simulacao (s)", simTime);
        cmd.AddValue ("nRuns", "Execucoes por combinacao", nRuns);
        cmd.Parse (argc, argv);

        std::vector<std::string> protocolos = {"AODV", "OLSR", "DSDV"};
        std::vector<double> velocidades = {1.0, 5.0, 10.0, 20.0};
        std::vector<double> areas = {500.0, 250.0};

        std::ofstream csv ("resultados-aodv-olsr-dsdv.csv");
        csv << "protocolo,area_m,velocidade_m_s,execucao,pacotes_enviados,pacotes_recebidos," "pdr_percent,atraso_medio_ms,throughput_mbps,jitter_medio_ms\n";

        for (const std::string &proto : protocolos){
        for (double area : areas){
            for (double v : velocidades){
                for (uint32_t run = 1; run <= nRuns; ++run){
                    std::cout << proto << " | Area " << area << "x" << area << " | vel " << v
                              << " m/s | execucao " << run << std::endl;
                    resultados r = ExecutaSimulacao (proto, nNodes, simTime, v, area, run);
                    csv << proto << "," << area << "," << v << "," << run << "," << r.tx << ","
                        << r.rx << "," << r.pdr << "," << r.atrasoMs << ","
                        << r.throughputMbps << "," << r.jitterMs << "\n";
                    csv.flush ();
                }
            }
        }
    }
    csv.close ();
    return 0;
    }