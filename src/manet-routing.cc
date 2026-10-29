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
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE ("ManetRoutingCompare");

struct resultados {
    uint32_t tx;
    uint32_t rx;
    uint64_t rxBytes;
    double pdr;
    double atrasoMs;
    double throughputKbps;
    double jitterMs;
};

// Conta os pacotes gerados pela aplicacao. O txPackets do FlowMonitor nao serve de
// denominador do PDR: ele so ve pacotes que chegam a camada IP, e o OLSR descarta no
// socket os pacotes sem rota, enquanto AODV e DSDV os enfileiram (e sao contados).
static void ContaPacoteGerado (uint32_t *contador, Ptr<const Packet>){
    ++(*contador);
}

resultados ExecutaSimulacao (const std::string &protocolo, uint32_t nNodes, double simTime, double velocidade, double area, double alcance, uint32_t run){
    RngSeedManager::SetSeed (1);
    RngSeedManager::SetRun (run);

    //1.Nos
    NodeContainer nodes;
    nodes.Create (nNodes);

    //2. wifi ad hoc: 802.11b com alcance fixo (modelo de disco). Com o canal padrao do
    // ns-3.48 (802.11ax, LogDistance e limiar de preambulo de -82 dBm) o alcance seria ~51 m.
    YansWifiChannelHelper channel;
    channel.SetPropagationDelay ("ns3::ConstantSpeedPropagationDelayModel");
    channel.AddPropagationLoss ("ns3::RangePropagationLossModel", "MaxRange", DoubleValue (alcance));
    YansWifiPhyHelper phy;
    phy.SetChannel (channel.Create ());
    WifiMacHelper mac;
    mac.SetType ("ns3::AdhocWifiMac");
    WifiHelper wifi;
    wifi.SetStandard (WIFI_STANDARD_80211b);
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
    const double inicioClientes = 10.0;
    const double intervaloPacotes = 1.0;
    // Mantem a aplicacao gerando trafego durante toda a janela util, inclusive
    // quando simTime for aumentado para experimentos mais longos.
    const uint32_t maxPacotes = std::max<uint32_t> (
        1, static_cast<uint32_t> (std::ceil (std::max (0.0, simTime - inicioClientes) / intervaloPacotes)));
    uint16_t portas[nFluxos] = {9, 10, 11, 12};
    uint32_t pacotesGerados = 0;
    // Fluxos que comecam no mesmo instante e tem o mesmo periodo transmitem sempre juntos e
    // colidem de forma artificial; cada cliente ganha um atraso inicial aleatorio em [0, intervalo).
    // Stream fixo: o sorteio muda a cada repeticao, mas e o mesmo para os tres protocolos.
    Ptr<UniformRandomVariable> atrasoInicial = CreateObject<UniformRandomVariable> ();
    atrasoInicial->SetAttribute ("Max", DoubleValue (intervaloPacotes));
    atrasoInicial->SetStream (0);
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
        echoClient.SetAttribute ("MaxPackets", UintegerValue (maxPacotes));
        echoClient.SetAttribute ("Interval", TimeValue (Seconds (intervaloPacotes)));
        echoClient.SetAttribute ("PacketSize", UintegerValue (512));
        ApplicationContainer clientApp = echoClient.Install (nodes.Get (origem));
        clientApp.Start (Seconds (inicioClientes + atrasoInicial->GetValue ()));
        clientApp.Stop (Seconds (simTime));
        clientApp.Get (0)->TraceConnectWithoutContext ("Tx", MakeBoundCallback (&ContaPacoteGerado, &pacotesGerados));
        clientApps.Add (clientApp);
    }

    //7. Coleta de dados
    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll ();

    Simulator::Stop (Seconds (simTime));
    Simulator::Run ();

    monitor->CheckForLostPackets ();
    Ptr<Ipv4FlowClassifier> classifier = DynamicCast<Ipv4FlowClassifier> (flowmon.GetClassifier ());

    resultados r = {pacotesGerados, 0, 0, 0.0, 0.0, 0.0, 0.0};
    double somaAtraso = 0.0;
    double somaJitter = 0.0;
    uint64_t amostrasJitter = 0;
    std::set<uint16_t> portasFluxos = {9, 10, 11, 12};
    for (auto &par : monitor->GetFlowStats ()){
        Ipv4FlowClassifier::FiveTuple t = classifier->FindFlow (par.first);
        if (portasFluxos.count (t.destinationPort) > 0){
            r.rx += par.second.rxPackets;
            r.rxBytes += par.second.rxBytes;
            somaAtraso += par.second.delaySum.GetSeconds ();
            somaJitter += par.second.jitterSum.GetSeconds ();
            if (par.second.rxPackets > 1){
                amostrasJitter += par.second.rxPackets - 1;
            }
        }
    }
    // Sem amostras a metrica fica indefinida (nan no CSV), em vez de 0, para nao puxar a media para baixo
    const double semAmostra = std::numeric_limits<double>::quiet_NaN ();
    double duracaoUtil = std::max (0.0, simTime - inicioClientes);
    r.pdr = (r.tx > 0) ? 100.0 * r.rx / r.tx : semAmostra;
    r.atrasoMs = (r.rx > 0) ? 1000.0 * somaAtraso / r.rx : semAmostra;
    r.throughputKbps = (duracaoUtil > 0) ? (r.rxBytes * 8.0) / (duracaoUtil * 1e3) : semAmostra;
    r.jitterMs = (amostrasJitter > 0) ? 1000.0 * somaJitter / amostrasJitter : semAmostra;

    Simulator::Destroy ();
    return r;
};

    int main (int argc, char *argv[]){
        uint32_t nNodes = 20;
        double simTime = 120.0;
        uint32_t nRuns = 5;
        double alcance = 250.0;
        std::string protocoloFiltro;
        double areaFiltro = 0.0;
        double velocidadeFiltro = 0.0;
        uint32_t runFiltro = 0;
        std::string arquivoSaida = "resultados-aodv-olsr-dsdv.csv";

        CommandLine cmd (__FILE__);
        cmd.AddValue ("nNodes", "Numero de nos", nNodes);
        cmd.AddValue ("simTime", "Tempo de simulacao (s)", simTime);
        cmd.AddValue ("nRuns", "Execucoes por combinacao", nRuns);
        cmd.AddValue ("alcance", "Alcance do radio em metros (modelo de disco)", alcance);
        cmd.AddValue ("protocolo", "Executa somente este protocolo (AODV, OLSR ou DSDV)", protocoloFiltro);
        cmd.AddValue ("area", "Executa somente esta area, em metros (0 = todas)", areaFiltro);
        cmd.AddValue ("velocidade", "Executa somente esta velocidade, em m/s (0 = todas)", velocidadeFiltro);
        cmd.AddValue ("run", "Executa somente esta repeticao (0 = todas)", runFiltro);
        cmd.AddValue ("output", "Arquivo CSV de saida", arquivoSaida);
        cmd.Parse (argc, argv);

        std::vector<std::string> protocolos = {"AODV", "OLSR", "DSDV"};
        std::vector<double> velocidades = {1.0, 5.0, 10.0, 20.0};
        std::vector<double> areas = {500.0, 250.0};

        std::ofstream csv (arquivoSaida);
        if (!csv.is_open ()){
            NS_FATAL_ERROR ("Nao foi possivel abrir o arquivo de saida: " << arquivoSaida);
        }
        csv << "protocolo,area_m,velocidade_m_s,execucao,pacotes_enviados,pacotes_recebidos," "pdr_percent,atraso_medio_ms,throughput_kbps,jitter_medio_ms\n";

        for (const std::string &proto : protocolos){
        if (!protocoloFiltro.empty () && proto != protocoloFiltro){
            continue;
        }
        for (double area : areas){
            if (areaFiltro > 0.0 && area != areaFiltro){
                continue;
            }
            for (double v : velocidades){
                if (velocidadeFiltro > 0.0 && v != velocidadeFiltro){
                    continue;
                }
                const uint32_t primeiroRun = runFiltro > 0 ? runFiltro : 1;
                const uint32_t ultimoRun = runFiltro > 0 ? runFiltro : nRuns;
                for (uint32_t run = primeiroRun; run <= ultimoRun; ++run){
                    std::cout << proto << " | Area " << area << "x" << area << " | vel " << v
                              << " m/s | execucao " << run << std::endl;
                    resultados r = ExecutaSimulacao (proto, nNodes, simTime, v, area, alcance, run);
                    csv << proto << "," << area << "," << v << "," << run << "," << r.tx << ","
                        << r.rx << "," << r.pdr << "," << r.atrasoMs << ","
                        << r.throughputKbps << "," << r.jitterMs << "\n";
                    csv.flush ();
                }
            }
        }
    }
    csv.close ();
    return 0;
    }
