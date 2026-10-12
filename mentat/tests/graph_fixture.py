"""Small report-shaped facts with deliberate summary duplicates and shared PACs."""
from collections import defaultdict


GIVING = [
    ('Harborlight Credit Union','Chief','Candidate','C00000001',100),
    ('Harborlight Credit Union','Chief','JFC','C00000002',200),
    ('Harborlight Credit Union','Middle Management','Super PAC','C00000003',300),
    ('Civic Horizon Bank','Employee','Super PAC','C00000003',400),
    ('Civic Horizon Bank','Chief','Trade Association','C00000004',50),
    ('Harborlight Credit Union','Corporate','Super PAC','C00000003',500),
    ('Harborlight Credit Union','Employee','Leadership PAC','C00000005',25),
    ('Civic Horizon Bank','Employee','Candidate','C00000001',-10),
    ('Civic Horizon Bank','Chief','Candidate','C00000006',20),
]


def add_graph_sheets(book):
    sheet = book.create_sheet('2025-2026')
    for row in [['Credit union giving sample'],['Same receipts in three fact families'],[],
                ['Fact Type','Company','Employee Tier','Amount','Recipient Category','Recipient Key','Include in Analysis']]:
        sheet.append(row)
    totals = defaultdict(int)
    for company,tier,category,committee,value in GIVING:
        totals[company,tier,category] += value
        sheet.append(['Recipient',company,tier,value,category,'FEC:'+committee,'Yes'])
    for (company,tier,category),value in totals.items():
        sheet.append(['Master',company,tier,value,category,None,'Yes'])
    sheet.append(['Employee','Harborlight Credit Union','Chief',99999,None,None,'Yes'])
    sheet.append(['Recipient','Harborlight Credit Union','Chief',99999,'Super PAC','FEC:C00000003','No'])
    maps = book.create_sheet('PAC & Candidate Map')
    for row in [['Mappings'],[],[],[],['Cycle','Committee ID','Recipient Committee','Recipient Category',
                                     'Direct Candidate / Sponsor','Candidate ID(s)','Direct Party','Relationship']]:
        maps.append(row)
    for committee,name,category,relationship in [
        ('C00000001','Candidate One Committee','Candidate','Principal campaign committee'),
        ('C00000002','Community Finance Victory JFC','JFC','Joint fundraiser linked to candidate'),
        ('C00000003','Members First Super PAC','Super PAC','No direct candidate/officeholder link'),
        ('C00000004','Cooperative Finance Association','Trade Association','No direct candidate/officeholder link'),
        ('C00000005','Harborlight Leadership PAC','Leadership PAC','Leadership PAC sponsor'),
        ('C00000006','Unauthorized Committee','Candidate','Unauthorized committee linked to candidate')]:
        maps.append(['2025-2026',committee,name,category,'Candidate One','H0CA00001','D',relationship])
    downstream = book.create_sheet('Downstream Activity')
    for row in [['Downstream'],[],['100 source rows condensed to 4 committee-candidate-activity groups'],[],
                ['Cycle','Source Committee ID','Candidate ID','Candidate / Officeholder','Candidate Party','Support / Oppose','Net Included Amount'],
                ['2025-2026','C00000003','H0CA00001','Candidate One','D','Support',777],
                ['2025-2026','C00000003','H0CA00001','Candidate One','D','Oppose',999],
                ['2025-2026','C00000003','H0CA00001','Candidate One','D','Contribution / Other',111],
                ['2025-2026','C00000002','H0CA00001','Candidate One','D','Support',33]]:
        downstream.append(row)
