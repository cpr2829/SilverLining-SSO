from django.shortcuts import render
from django.conf import settings
from django.contrib.auth.decorators import login_required

# Create your views here.
@login_required
def index(request):
	context = {
	'cas_url' : settings.CAS_SERVER_URL,
	'openstack_url' :settings.OPENSTACK_URL	
	}
    	return render(request, 'SilverLining/header.html', context)

