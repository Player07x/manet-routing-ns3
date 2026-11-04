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
#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <map>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE ("ManetRoutingCompare");

struct metricas {
    uint32_t tx;          // pacotes gerados pela aplicacao
    uint32_t txIp;        // pacotes que chegaram a camada IP (txPackets do FlowMonitor)
    uint32_t rx;
    uint32_t perdidos;    // lostPackets do FlowMonitor
    uint64_t rxBytes;
    uint32_t descartesMac; // quadros de dados descartados pelo MAC ao esgotar as retransmissoes
    double pdr;
    double atrasoMs;
    double throughputMbps;
    double jitterMs;
    double atrasoP50Ms;   // mediana e percentil 95 do atraso, pelo histograma do FlowMonitor
    double atrasoP95Ms;
};

struct fluxo {
    std::string origem;
    std::string destino;
    metricas m;
};

struct resultados {
    metricas total;
    std::vector<fluxo> fluxos;
};

// Conta os pacotes gerados pela aplicacao. O txPackets do FlowMonitor nao serve de
// denominador do PDR: ele so ve pacotes que chegam a camada IP, e o OLSR descarta no
// socket os pacotes sem rota, enquanto AODV e DSDV os enfileiram (e sao contados).
static void ContaPacoteGerado (uint32_t *contador, Ptr<const Packet>){
    ++(*contador);
}

// Registro opcional do atraso de cada pacote recebido (--atrasosPacotes), para diagnostico.
// O identificador do pacote (UID) e o mesmo no envio pelo cliente e na recepcao pelo servidor.
static std::ofstream g_arqPacotes;
static std::string g_prefixoPacotes;
static std::map<uint64_t, std::pair<uint32_t, double>> g_envioPacotes; // UID -> (fluxo, instante de envio)
static void RegistraEnvio (uint32_t fluxo, Ptr<const Packet> p){
    g_envioPacotes[p->GetUid ()] = {fluxo, Simulator::Now ().GetSeconds ()};
}
static void RegistraRecepcao (Ptr<const Packet> p, const Address &){
    auto it = g_envioPacotes.find (p->GetUid ());
    if (it != g_envioPacotes.end ()){
        g_arqPacotes << g_prefixoPacotes << "," << it->second.first << "," << it->second.second << ","
                     << 1000.0 * (Simulator::Now ().GetSeconds () - it->second.second) << "\n";
        g_envioPacotes.erase (it);
    }
}

// Quadros de dados (pedidos dos 4 fluxos, por porta de destino) que o MAC descartou ao atingir o
// limite de retransmissoes: o proximo salto nao respondeu, em geral porque ja saiu do alcance.
static std::map<uint16_t, uint32_t> g_descartesMac;
static void ContaDescarteMac (WifiMacDropReason motivo, Ptr<const WifiMpdu> mpdu){
    if (motivo != WIFI_MAC_DROP_REACHED_RETRY_LIMIT || !mpdu->GetHeader ().IsData ()){
        return;
    }
    Ptr<Packet> p = mpdu->GetPacket ()->Copy ();
    LlcSnapHeader llc;
    Ipv4Header ip;
    UdpHeader udp;
    if (p->RemoveHeader (llc) == 0 || llc.GetType () != 0x0800 || p->RemoveHeader (ip) == 0
        || ip.GetProtocol () != UdpL4Protocol::PROT_NUMBER || p->PeekHeader (udp) == 0){
        return;
    }
    ++g_descartesMac[udp.GetDestinationPort ()];
}

static const double LARGURA_HISTOGRAMA_S = 0.0001; // resolucao de 0,1 ms

// Percentil q (0-1) do atraso, em ms, a partir das contagens do histograma do FlowMonitor
static double PercentilAtraso (const std::vector<uint64_t> &contagens, double q){
    uint64_t total = 0;
    for (uint64_t c : contagens){
        total += c;
    }
    if (total == 0){
        return std::numeric_limits<double>::quiet_NaN ();
    }
    uint64_t acumulado = 0;
    for (size_t i = 0; i < contagens.size (); ++i){
        acumulado += contagens[i];
        if (acumulado >= q * total){
            return 1000.0 * (i + 0.5) * LARGURA_HISTOGRAMA_S;
        }
    }
    return 1000.0 * (contagens.size () - 0.5) * LARGURA_HISTOGRAMA_S;
}

static void SomaHistograma (std::vector<uint64_t> &contagens, const Histogram &h){
    if (contagens.size () < h.GetNBins ()){
        contagens.resize (h.GetNBins (), 0);
    }
    for (uint32_t i = 0; i < h.GetNBins (); ++i){
        contagens[i] += h.GetBinCount (i);
    }
}

// Sem amostras a metrica fica indefinida (nan no CSV), em vez de 0, para nao puxar a media para baixo
static void CalculaMetricas (metricas &m, double somaAtraso, double somaJitter, uint64_t amostrasJitter, double duracaoUtil,
                             const std::vector<uint64_t> &histograma){
    const double semAmostra = std::numeric_limits<double>::quiet_NaN ();
    m.pdr = (m.tx > 0) ? 100.0 * m.rx / m.tx : semAmostra;
    m.atrasoMs = (m.rx > 0) ? 1000.0 * somaAtraso / m.rx : semAmostra;
    m.throughputMbps = (duracaoUtil > 0) ? (m.rxBytes * 8.0) / (duracaoUtil * 1e6) : semAmostra;
    m.jitterMs = (amostrasJitter > 0) ? 1000.0 * somaJitter / amostrasJitter : semAmostra;
    m.atrasoP50Ms = PercentilAtraso (histograma, 0.50);
    m.atrasoP95Ms = PercentilAtraso (histograma, 0.95);
}

// Pre-preenche as tabelas ARP com entradas permanentes (IP -> MAC de todos os nos), para que a
// resolucao ARP nao interfira na comparacao dos protocolos de roteamento. No ARP padrao do ns-3.48
// as retransmissoes de requisicao saem a cada 1 s exato, sem jitter: requisicoes de nos vizinhos
// que comecam no mesmo instante colidem em todas as tentativas, e o vizinho fica "morto" por
// 100 s (DeadTimeout), descartando todos os pacotes destinados a ele. As entradas precisam ser
// permanentes (e nao as AutoGenerated do NeighborCacheHelper): o AODV so usa entradas Alive ou
// Permanent para associar falhas de transmissao do MAC aos vizinhos e detectar enlaces quebrados.
static void PreencheArpPermanente (NodeContainer &nodes, NetDeviceContainer &devices, Ipv4InterfaceContainer &interfaces){
    for (uint32_t i = 0; i < nodes.GetN (); ++i){
        Ptr<ArpCache> cache = nodes.Get (i)->GetObject<Ipv4L3Protocol> ()->GetInterface (1)->GetArpCache ();
        for (uint32_t j = 0; j < nodes.GetN (); ++j){
            if (i != j){
                ArpCache::Entry *entrada = cache->Add (interfaces.GetAddress (j));
                entrada->SetMacAddress (devices.Get (j)->GetAddress ());
                entrada->MarkPermanent ();
            }
        }
    }
}

resultados ExecutaSimulacao (const std::string &protocolo, uint32_t nNodes, double simTime, double velocidade, double area, double alcance, const std::string &arp, uint32_t seed, uint32_t run){
    RngSeedManager::SetSeed (seed);
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
    if (arp == "permanente"){
        PreencheArpPermanente (nodes, devices, interfaces);
    }

    //6. Aplicativo UDP - 4 fluxos simultaneos (exigido pelo cenario-base)
    const uint32_t nFluxos = 4;
    const double inicioClientes = 10.0;
    const double intervaloPacotes = 1.0;
    // Mantem a aplicacao gerando trafego durante toda a janela util, inclusive
    // quando simTime for aumentado para experimentos mais longos.
    const uint32_t maxPacotes = std::max<uint32_t> (
        1, static_cast<uint32_t> (std::ceil (std::max (0.0, simTime - inicioClientes) / intervaloPacotes)));
    uint16_t portas[nFluxos] = {9, 10, 11, 12};
    uint32_t pacotesGerados[nFluxos] = {0, 0, 0, 0};
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
        if (g_arqPacotes.is_open ()){
            serverApp.Get (0)->TraceConnectWithoutContext ("Rx", MakeCallback (&RegistraRecepcao));
        }
        serverApps.Add (serverApp);

        UdpEchoClientHelper echoClient (interfaces.GetAddress (destino), portas[f]);
        echoClient.SetAttribute ("MaxPackets", UintegerValue (maxPacotes));
        echoClient.SetAttribute ("Interval", TimeValue (Seconds (intervaloPacotes)));
        echoClient.SetAttribute ("PacketSize", UintegerValue (512));
        ApplicationContainer clientApp = echoClient.Install (nodes.Get (origem));
        clientApp.Start (Seconds (inicioClientes + atrasoInicial->GetValue ()));
        clientApp.Stop (Seconds (simTime));
        clientApp.Get (0)->TraceConnectWithoutContext ("Tx", MakeBoundCallback (&ContaPacoteGerado, &pacotesGerados[f]));
        if (g_arqPacotes.is_open ()){
            clientApp.Get (0)->TraceConnectWithoutContext ("Tx", MakeBoundCallback (&RegistraEnvio, f + 1));
        }
        clientApps.Add (clientApp);
    }

    //7. Coleta de dados
    g_descartesMac.clear ();
    Config::ConnectWithoutContext ("/NodeList/*/DeviceList/*/$ns3::WifiNetDevice/Mac/DroppedMpdu",
                                   MakeCallback (&ContaDescarteMac));
    FlowMonitorHelper flowmon;
    flowmon.SetMonitorAttribute ("DelayBinWidth", DoubleValue (LARGURA_HISTOGRAMA_S));
    Ptr<FlowMonitor> monitor = flowmon.InstallAll ();

    Simulator::Stop (Seconds (simTime));
    Simulator::Run ();

    monitor->CheckForLostPackets ();
    Ptr<Ipv4FlowClassifier> classifier = DynamicCast<Ipv4FlowClassifier> (flowmon.GetClassifier ());

    // Duracao util do throughput: do inicio dos clientes (10 s) ao fim da simulacao
    const double duracaoUtil = std::max (0.0, simTime - inicioClientes);
    resultados r;
    r.total = metricas ();
    double somaAtraso = 0.0, somaJitter = 0.0;
    uint64_t amostrasJitter = 0;
    std::vector<uint64_t> histogramaTotal;
    for (uint32_t f = 0; f < nFluxos; ++f){
        fluxo fl;
        std::ostringstream origem, destino;
        origem << interfaces.GetAddress (f);
        destino << interfaces.GetAddress (nNodes - 1 - f);
        fl.origem = origem.str ();
        fl.destino = destino.str ();
        fl.m = metricas ();
        fl.m.tx = pacotesGerados[f];
        fl.m.descartesMac = g_descartesMac[portas[f]];
        double atrasoFluxo = 0.0, jitterFluxo = 0.0;
        std::vector<uint64_t> histogramaFluxo;
        // Respostas do echo (porta de destino efemera) ficam de fora: so contam os pedidos de cada fluxo
        for (auto &par : monitor->GetFlowStats ()){
            Ipv4FlowClassifier::FiveTuple t = classifier->FindFlow (par.first);
            if (t.destinationPort == portas[f] && t.sourceAddress == interfaces.GetAddress (f)){
                fl.m.txIp += par.second.txPackets;
                fl.m.rx += par.second.rxPackets;
                fl.m.perdidos += par.second.lostPackets;
                fl.m.rxBytes += par.second.rxBytes;
                atrasoFluxo += par.second.delaySum.GetSeconds ();
                jitterFluxo += par.second.jitterSum.GetSeconds ();
                SomaHistograma (histogramaFluxo, par.second.delayHistogram);
                SomaHistograma (histogramaTotal, par.second.delayHistogram);
            }
        }
        // Jitter do fluxo: jitterSum / (pacotes recebidos - 1)
        const uint64_t amostrasFluxo = fl.m.rx > 1 ? fl.m.rx - 1 : 0;
        CalculaMetricas (fl.m, atrasoFluxo, jitterFluxo, amostrasFluxo, duracaoUtil, histogramaFluxo);
        r.fluxos.push_back (fl);

        r.total.tx += fl.m.tx;
        r.total.txIp += fl.m.txIp;
        r.total.rx += fl.m.rx;
        r.total.perdidos += fl.m.perdidos;
        r.total.rxBytes += fl.m.rxBytes;
        r.total.descartesMac += fl.m.descartesMac;
        somaAtraso += atrasoFluxo;
        somaJitter += jitterFluxo;
        amostrasJitter += amostrasFluxo;
    }
    // Jitter da execucao: soma dos jitterSum dividida pelos pares de pacotes consecutivos de cada fluxo
    CalculaMetricas (r.total, somaAtraso, somaJitter, amostrasJitter, duracaoUtil, histogramaTotal);

    Simulator::Destroy ();
    return r;
};

    int main (int argc, char *argv[]){
        uint32_t nNodes = 20;
        double simTime = 120.0;
        uint32_t nRuns = 5;
        double alcance = 250.0;
        uint32_t seed = 1;
        std::string arp = "permanente";
        std::string protocoloFiltro;
        double areaFiltro = 0.0;
        double velocidadeFiltro = 0.0;
        uint32_t runFiltro = 0;
        std::string arquivoSaida = "resultados-aodv-olsr-dsdv.csv";
        std::string arquivoFluxos = "resultados-por-fluxo.csv";
        std::string arquivoPacotes;

        CommandLine cmd (__FILE__);
        cmd.AddValue ("nNodes", "Numero de nos", nNodes);
        cmd.AddValue ("simTime", "Tempo de simulacao (s)", simTime);
        cmd.AddValue ("nRuns", "Execucoes por combinacao", nRuns);
        cmd.AddValue ("alcance", "Alcance do radio em metros (modelo de disco)", alcance);
        cmd.AddValue ("seed", "Seed mestre do gerador de numeros aleatorios", seed);
        cmd.AddValue ("arp", "permanente (tabelas ARP pre-preenchidas) ou dinamico (ARP padrao do ns-3)", arp);
        cmd.AddValue ("protocolo", "Executa somente este protocolo (AODV, OLSR ou DSDV)", protocoloFiltro);
        cmd.AddValue ("area", "Executa somente esta area, em metros (0 = todas)", areaFiltro);
        cmd.AddValue ("velocidade", "Executa somente esta velocidade, em m/s (0 = todas)", velocidadeFiltro);
        cmd.AddValue ("run", "Executa somente esta repeticao (0 = todas)", runFiltro);
        cmd.AddValue ("output", "Arquivo CSV de saida (uma linha por execucao)", arquivoSaida);
        cmd.AddValue ("outputFluxos", "Arquivo CSV de saida (uma linha por fluxo)", arquivoFluxos);
        cmd.AddValue ("atrasosPacotes", "Opcional: CSV com o atraso de cada pacote recebido (vazio = nao grava)", arquivoPacotes);
        cmd.Parse (argc, argv);
        if (arp != "permanente" && arp != "dinamico"){
            NS_FATAL_ERROR ("Valor invalido para --arp: " << arp << " (use permanente ou dinamico)");
        }

        std::vector<std::string> protocolos = {"AODV", "OLSR", "DSDV"};
        std::vector<double> velocidades = {1.0, 5.0, 10.0, 20.0};
        // 500 m: cenario-base; 750 m: extensao do grupo (area maior, menor densidade de nos)
        std::vector<double> areas = {500.0, 750.0};

        std::ofstream csv (arquivoSaida);
        std::ofstream csvFluxos (arquivoFluxos);
        if (!csv.is_open () || !csvFluxos.is_open ()){
            NS_FATAL_ERROR ("Nao foi possivel abrir os arquivos de saida: " << arquivoSaida << ", " << arquivoFluxos);
        }
        csv << "protocolo,area_m,velocidade_m_s,seed,execucao,pacotes_enviados,pacotes_recebidos,"
               "pdr_percent,atraso_medio_ms,throughput_mbps,jitter_medio_ms,atraso_p50_ms,atraso_p95_ms,"
               "pacotes_tx_ip,descartes_mac\n";
        csvFluxos << "protocolo,area_m,velocidade_m_s,seed,execucao,fluxo,origem,destino,pacotes_enviados,"
                     "pacotes_tx_ip,pacotes_recebidos,pacotes_perdidos,bytes_recebidos,pdr_percent,"
                     "atraso_medio_ms,throughput_mbps,jitter_medio_ms,atraso_p50_ms,atraso_p95_ms,descartes_mac\n";
        csv.precision (10);
        csvFluxos.precision (10);
        if (!arquivoPacotes.empty ()){
            g_arqPacotes.open (arquivoPacotes);
            if (!g_arqPacotes.is_open ()){
                NS_FATAL_ERROR ("Nao foi possivel abrir " << arquivoPacotes);
            }
            g_arqPacotes.precision (10);
            g_arqPacotes << "protocolo,area_m,velocidade_m_s,seed,execucao,fluxo,envio_s,atraso_ms\n";
        }

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
                    std::ostringstream prefixo;
                    prefixo << proto << "," << area << "," << v << "," << seed << "," << run;
                    g_prefixoPacotes = prefixo.str ();
                    g_envioPacotes.clear ();
                    resultados r = ExecutaSimulacao (proto, nNodes, simTime, v, area, alcance, arp, seed, run);
                    const metricas &m = r.total;
                    csv << proto << "," << area << "," << v << "," << seed << "," << run << "," << m.tx << ","
                        << m.rx << "," << m.pdr << "," << m.atrasoMs << ","
                        << m.throughputMbps << "," << m.jitterMs << "," << m.atrasoP50Ms << "," << m.atrasoP95Ms << ","
                        << m.txIp << "," << m.descartesMac << "\n";
                    for (uint32_t f = 0; f < r.fluxos.size (); ++f){
                        const metricas &mf = r.fluxos[f].m;
                        csvFluxos << proto << "," << area << "," << v << "," << seed << "," << run << "," << f + 1 << ","
                                  << r.fluxos[f].origem << "," << r.fluxos[f].destino << "," << mf.tx << ","
                                  << mf.txIp << "," << mf.rx << "," << mf.perdidos << "," << mf.rxBytes << ","
                                  << mf.pdr << "," << mf.atrasoMs << "," << mf.throughputMbps << "," << mf.jitterMs << ","
                                  << mf.atrasoP50Ms << "," << mf.atrasoP95Ms << "," << mf.descartesMac << "\n";
                    }
                    csv.flush ();
                    csvFluxos.flush ();
                }
            }
        }
    }
    csv.close ();
    csvFluxos.close ();
    if (g_arqPacotes.is_open ()){
        g_arqPacotes.close ();
    }
    return 0;
    }
